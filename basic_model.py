import numpy as np
import random
import json
import h5py
import matplotlib.pyplot as plt
from  skimage import io, color, img_as_float
from skimage.exposure import adjust_gamma
from skimage.segmentation import mark_boundaries
from sklearn.feature_extraction.image import extract_patches_2d
from sklearn.metrics import classification_report
from keras.models import Sequential, Graph, model_from_json
from keras.layers.convolutional import Convolution2D, MaxPooling2D
from keras.layers.core import Dense, Dropout, Activation, Flatten, Merge, Reshape, MaxoutDense
from keras.layers.normalization import BatchNormalization
from keras.regularizers import l1l2
from keras.optimizers import SGD
from keras.constraints import maxnorm
from keras.callbacks import EarlyStopping, ModelCheckpoint
from keras.utils import np_utils

class BasicModel(object):
    def __init__(self, n_epoch=10, n_chan=4, batch_size=128, loaded_model=False, architecture='single', w_reg=0.01):
        '''
        INPUT   (1) int 'n_epoch': number of eopchs to train on. defaults to 10
                (2) int 'n_chan': number of channels being assessed. defaults to 4
                (3) int 'batch_size': number of images to train on for each batch. defaults to 128
                (4) bool 'loaded_model': True if loading a pre-existing model. defaults to False
                (5) str 'architecture': type of model to use, options = single, dual, or two_path. defaults to single (only currently optimized version)
                (6) float 'w_reg': value for l1 and l2 regularization. defaults to 0.01
        '''
        self.n_epoch = n_epoch
        self.n_chan = n_chan
        self.batch_size = batch_size
        self.architecture = architecture
        self.loaded_model = loaded_model
        self.w_reg = w_reg
        if not self.loaded_model:
            if self.architecture == 'two_path':
                self.model_comp = self.comp_two_path()
            elif self.architecture == 'dual':
                self.model_comp = self.comp_double()
            else:
                self.model_comp = self.compile_model()

    def compile_model(self):
        '''
        compiles standard single model with 4 convolitional/max-pooling layers.
        '''
        print 'Compiling single model...'
        single = Sequential()

        single.add(Convolution2D(64, 7, 7, border_mode='valid', W_regularizer=l1l2(l1=self.w_reg, l2=self.w_reg), input_shape=(self.n_chan,33,33)))
        single.add(Activation('relu'))
        single.add(BatchNormalization(mode=0, axis=1))
        single.add(MaxPooling2D(pool_size=(2,2), strides=(1,1)))
        single.add(Dropout(0.5))
        single.add(Convolution2D(nb_filter=128, nb_row=5, nb_col=5, activation='relu', border_mode='valid', W_regularizer=l1l2(l1=self.w_reg, l2=self.w_reg)))
        single.add(BatchNormalization(mode=0, axis=1))
        single.add(MaxPooling2D(pool_size=(2,2), strides=(1,1)))
        single.add(Dropout(0.5))
        single.add(Convolution2D(nb_filter=128, nb_row=5, nb_col=5, activation='relu', border_mode='valid', W_regularizer=l1l2(l1=self.w_reg, l2=self.w_reg)))
        single.add(BatchNormalization(mode=0, axis=1))
        single.add(MaxPooling2D(pool_size=(2,2), strides=(1,1)))
        single.add(Dropout(0.5))
        single.add(Convolution2D(nb_filter=128, nb_row=3, nb_col=3, activation='relu', border_mode='valid', W_regularizer=l1l2(l1=self.w_reg, l2=self.w_reg)))
        single.add(Dropout(0.25))

        single.add(Flatten())
        single.add(Dense(5))
        single.add(Activation('softmax'))

        sgd = SGD(lr=0.001, decay=0.01, momentum=0.9)
        single.compile(loss='categorical_crossentropy', optimizer='sgd')
        print 'Done.'
        return single

    def comp_two_path(self):
        '''
        compiles two-path model, takes in a 4x33x33 patch and assesses global and local paths, then merges the results.
        '''
        print 'Compiling two-path model...'
        model = Graph()
        model.add_input(name='input', input_shape=(self.n_chan, 33, 33))

        # local pathway, first convolution/pooling
        model.add_node(Convolution2D(64, 7, 7, border_mode='valid', activation='relu', W_regularizer=l1l2(l1=0.01, l2=0.01)), name='local_c1', input= 'input')
        model.add_node(MaxPooling2D(pool_size=(4,4), strides=(1,1), border_mode='valid'), name='local_p1', input='local_c1')

        # local pathway, second convolution/pooling
        model.add_node(Dropout(0.5), name='drop_lp1', input='local_p1')
        model.add_node(Convolution2D(64, 3, 3, border_mode='valid', activation='relu', W_regularizer=l1l2(l1=0.01, l2=0.01)), name='local_c2', input='drop_lp1')
        model.add_node(MaxPooling2D(pool_size=(2,2), strides=(1,1), border_mode='valid'), name='local_p2', input='local_c2')

        # global pathway
        model.add_node(Convolution2D(160, 13, 13, border_mode='valid', activation='relu', W_regularizer=l1l2(l1=0.01, l2=0.01)), name='global', input='input')

        # merge local and global pathways
        model.add_node(Dropout(0.5), name='drop_lp2', input='local_p2')
        model.add_node(Dropout(0.5), name='drop_g', input='global')
        model.add_node(Convolution2D(5, 21, 21, border_mode='valid', activation='relu',  W_regularizer=l1l2(l1=0.01, l2=0.01)), name='merge', inputs=['drop_lp2', 'drop_g'], merge_mode='concat', concat_axis=1)

        # Flatten output of 5x1x1 to 1x5, perform softmax
        model.add_node(Flatten(), name='flatten', input='merge')
        model.add_node(Dense(5, activation='softmax'), name='dense_output', input='flatten')
        model.add_output(name='output', input='dense_output')

        sgd = SGD(lr=0.005, decay=0.1, momentum=0.9)
        model.compile('sgd', loss={'output':'categorical_crossentropy'})
        print 'Done.'
        return model

    def comp_double(self):
        '''
        double model. Simialar to two-pathway, except takes in a 4x33x33 patch and it's center 4x5x5 patch. merges paths at flatten layer.
        '''
        print 'Compiling double model...'
        single = Sequential()
        single.add(Convolution2D(64, 7, 7, border_mode='valid', W_regularizer=l1l2(l1=0.01, l2=0.01), input_shape=(4,33,33)))
        single.add(Activation('relu'))
        single.add(BatchNormalization(mode=0, axis=1))
        single.add(MaxPooling2D(pool_size=(2,2), strides=(1,1)))
        single.add(Dropout(0.5))
        single.add(Convolution2D(nb_filter=128, nb_row=5, nb_col=5, activation='relu', border_mode='valid', W_regularizer=l1l2(l1=0.01, l2=0.01)))
        single.add(BatchNormalization(mode=0, axis=1))
        single.add(MaxPooling2D(pool_size=(2,2), strides=(1,1)))
        single.add(Dropout(0.5))
        single.add(Convolution2D(nb_filter=256, nb_row=5, nb_col=5, activation='relu', border_mode='valid', W_regularizer=l1l2(l1=0.01, l2=0.01)))
        single.add(BatchNormalization(mode=0, axis=1))
        single.add(MaxPooling2D(pool_size=(2,2), strides=(1,1)))
        single.add(Dropout(0.5))
        single.add(Convolution2D(nb_filter=128, nb_row=3, nb_col=3, activation='relu', border_mode='valid', W_regularizer=l1l2(l1=0.01, l2=0.01)))
        single.add(Dropout(0.25))
        single.add(Flatten())

        # add small patch to train on
        five = Sequential()
        five.add(Reshape((100,1), input_shape = (4,5,5)))
        five.add(Flatten())
        five.add(MaxoutDense(128, nb_feature=5))
        five.add(Dropout(0.5))

        model = Sequential()
        # merge both paths
        model.add(Merge([five, single], mode='concat', concat_axis=1))
        model.add(Dense(5))
        model.add(Activation('softmax'))

        sgd = SGD(lr=0.001, decay=0.01, momentum=0.9)
        model.compile(loss='categorical_crossentropy', optimizer='sgd')
        print 'Done.'
        return model

    def load_model_weights(self, model_name):
        '''
        INPUT  (1) string 'model_name': filepath to model and weights, not including extension
        OUTPUT: Model with loaded weights. can fit on model using loaded_model=True in fit_model method
        '''
        model = '{}.json'.format(model_name)
        weights = '{}.h5'.format(model_name)
        with open(model_n) as f:
            m = f.next()
        self.model_load = model_from_json(json.loads(m))
        self.model.load_weights(weights)

    def fit_model(self, X_train, y_train, X5_train = None):
        '''
        INPUT   (1) numpy array 'X_train': list of patches to train on in form (n_sample, n_channel, h, w)
                (2) numpy vector 'y_train': list of labels corresponding to X_train patches in form (n_sample,)
                (3) numpy array 'X5_train': center 5x5 patch in corresponding X_train patch. if None, uses single-path architecture
        OUTPUT  (1) Fits specified model
        '''
        Y_train = np_utils.to_categorical(y_train, 5)

        shuffle = zip(X_train, Y_train)
        np.random.shuffle(shuffle)

        X_train = np.array([shuffle[i][0] for i in xrange(len(shuffle))])
        Y_train = np.array([shuffle[i][1] for i in xrange(len(shuffle))])
        es = EarlyStopping(monitor='val_loss', patience=2, verbose=1, mode='auto')

        # Save model after each epoch to check/bm_epoch#-val_loss
        checkpointer = ModelCheckpoint(filepath="./check/bm_{epoch:02d}-{val_loss:.2f}.hdf5", verbose=1)

        if self.architecture == 'dual':
            self.model_comp.fit([X5_train, X_train], Y_train, batch_size=self.batch_size, nb_epoch=self.n_epoch, validation_split=0.1, show_accuracy=True, verbose=1, callbacks=[checkpointer])
        elif self.architecture == 'two_path':
            data = {'input': X_train, 'output': Y_train}
            self.model_comp.fit(data, batch_size=self.batch_size, nb_epoch=self.n_epoch, validation_split=0.1, show_accuracy=True, verbose=1, callbacks=[checkpointer])
        else:
            self.model_comp.fit(X_train, Y_train, batch_size=self.batch_size, nb_epoch=self.n_epoch, validation_split=0.1, show_accuracy=True, verbose=1, callbacks=[checkpointer])

    def save_model(self, model_name):
        '''
        INPUT string 'model_name': name to save model and weigths under, including filepath but not extension
        Saves current model as json and weigts as h5df file
        '''
        model = '{}.json'.format(model_name)
        weights = '{}.hdf5'.formate(model_name)
        json_string = self.model_comp.to_json()
        self.model_comp.save_weights(weights)
        with open(model, 'w') as f:
            json.dump(json_string, f)

    def class_report(self, X_test, y_test):
        '''
        INPUT   (1) list 'X_test': test data of 4x33x33 patches
                (2) list 'y_test': labels for X_test
        OUTPUT  (1) confusion matrix of precision, recall and f1 score
        '''
        y_pred = self.model_comp.predict_class(X_test)
        print classification_report(y_pred, y_test)

    def predict_image(self, test_img, show=False):
        imgs = io.imread(test_img).astype('float').reshape(5,240,240)
        plist = []

        # create patches from an entire slice
        for img in imgs[:-1]:
            if np.max(img) != 0:
                img /= np.max(img)
            p = extract_patches_2d(img, (33,33))
            plist.append(p)
        patches = np.array(zip(np.array(plist[0]), np.array(plist[1]), np.array(plist[2]), np.array(plist[3])))

        # predict classes of each pixel based on model
        full_pred = self.model_comp.predict_classes(patches)
        fp1 = full_pred.reshape(208,208)
        if show:
            io.imshow(fp1)
            plt.show
        else:
            return fp1

    def show_segmented_image(self, test_img, modality='t1c'):
        modes = {'flair':0, 't1':1, 't1c':2, 't2':3}

        segmentation = self.predict_image(test_img, show=False)
        img_mask = np.pad(segmentation, (16,16), mode='edge')
        ones = np.argwhere(img_mask == 1)
        twos = np.argwhere(img_mask == 2)
        threes = np.argwhere(img_mask == 3)
        fours = np.argwhere(img_mask == 4)

        img_back =  io.imread(test_img).reshape(5,240,240)[modes[modality]]
        overlay = mark_boundaries(orig_img, seg_full)

        # adjust gamma of image
        image = adjust_gamma(color.gray2rgb(gray_img), 0.65)
        sliced_image = image.copy()
        red_multiplier = [1, 0.2, 0.2]
        yellow_multiplier = [1,1,0.25]
        green_multiplier = [0.35,0.75,0.25]
        blue_multiplier = [0,0.25,0.9]

        # change colors of segmented classes
        for i in xrange(len(ones)):
            blue_multiplier
            sliced_image[ones[i][0]][ones[i][1]] = red_multiplier
        for i in xrange(len(twos)):
            sliced_image[twos[i][0]][twos[i][1]] = green_multiplier
        for i in xrange(len(threes)):
            sliced_image[threes[i][0]][threes[i][1]] = blue_multiplier
        for i in xrange(len(fours)):
            sliced_image[fours[i][0]][fours[i][1]] = yellow_multiplier
