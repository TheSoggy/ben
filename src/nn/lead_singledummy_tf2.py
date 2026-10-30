import numpy as np
import tensorflow as tf
from tensorflow.keras.models import load_model
from nn.timing import ModelTimer
from nn.batch_pad import padded_size, pad, unpad

class LeadSingleDummy:

    def __init__(self, model_path):
        self.model_path = model_path
        self.model = self.load_model()

    def load_model(self):
        return load_model(self.model_path, compile=False)

    @tf.function(input_signature=[tf.TensorSpec(shape=[None, None, 298], dtype=tf.float16)])
    def pred_fun_tf(self, x):
        result = self.model(x, training=False)
        return result


    def pred_fun(self, x):
        n = len(x)
        with ModelTimer.time_call('single_dummy'):
            result = unpad(self.pred_fun_tf(pad(x, padded_size(n))), n)
        return result.numpy()

