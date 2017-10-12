from gcn.layers import *
from gcn.metrics import *
from gcn.inits import *
from time import time
import scipy.sparse as sp
from gcn.utils import sparse_to_tuple, dropout
from gcn.models import GCN
import numpy as np
from history import mean_history

flags = tf.app.flags
FLAGS = flags.FLAGS


class VRGCN(GCN):
    def __init__(self, data_per_fold, L, preprocess, placeholders, 
                 features, features1, adj,
                 **kwargs):
        super(VRGCN, self).__init__(data_per_fold, L, preprocess, placeholders, 
                                    features, features1, adj, **kwargs)
        self.run_t = 0
        self.g_t   = 0
        self.h_t   = 0
        self.g_ops = 0
        self.nn_ops = 0

    def _build_history(self):
        # Create history after each aggregation
        self.history_ph      = []
        self.history_mean_ph = []
        self.history         = []
        for i in range(self.L):
            dims = self.agg0_dim if i==0 else FLAGS.hidden1
            self.history_ph.append(tf.placeholder(tf.float32, name='hist{}_ph'.format(i)))
            self.history_mean_ph.append(tf.placeholder(tf.float32, name='hist_mean{}_ph'.format(i)))
            self.history.append(np.zeros((self.features.shape[0], dims), dtype=np.float32))

    def get_data(self, feed_dict, is_training):
        ids = feed_dict[self.placeholders['fields'][0]]
        if self.sparse_input:
            feed_dict[self.inputs_ph] = sparse_to_tuple(self.features[ids])
        else:
            feed_dict[self.inputs_ph] = self.features[ids]

        # Read history
        for l in range(self.L):
            ifield = feed_dict[self.placeholders['fields'][l]]
            ofield = feed_dict[self.placeholders['fields'][l+1]]
            feed_dict[self.history_ph[l]] = self.history[l][ifield]
            fadj   = self.adj[ofield]
            #feed_dict[self.history_mean_ph[l]] = fadj.dot(self.history[l])
            feed_dict[self.history_mean_ph[l]] = mean_history(fadj, self.history[l])
            self.g_ops += fadj.nnz * self.history[l].shape[1] * 2

        for c, l in self.layer_comp:
            self.nn_ops += c * feed_dict[self.placeholders['fields'][l]].shape[0] * 4

    def run_one_step(self, sess, feed_dict, is_training):
        t = time()
        self.get_data(feed_dict, is_training)
        self.g_t += time() - t

        # Run
        t = time()
        if is_training:
            outs, hist, values = sess.run([[self.opt_op, self.loss, self.accuracy], 
                                           self.history_ops, self.average_get_ops],
                                  feed_dict=feed_dict)
        else:
            outs, hist, values = sess.run([[self.loss, self.accuracy, self.pred],
                                           self.history_ops, self.average_get_ops],
                                     feed_dict=feed_dict)
        self.run_t += time() - t

        t = time()
        self.average_model(values)

        # Write history
        for l in range(self.L):
            field = feed_dict[self.placeholders['fields'][l]]
            self.history[l][field] = hist[l]
        self.h_t += time() - t

        return outs

    def get_pred_and_grad(self, sess, feed_dict, is_training):
        self.get_data(feed_dict, is_training)

        # Run
        pred, grads = sess.run([self.pred, self.grads], 
                               feed_dict=feed_dict)

        return pred, grads


    def _build_aggregators(self):
        adjs   = self.placeholders['adj']
        for l in range(self.L):
            self.aggregators.append(VRAggregator(adjs[l], self.history_ph[l],
                                             self.history_mean_ph[l], 
                                             self.placeholders['is_training'],
                                             name='agg%d'%l))

