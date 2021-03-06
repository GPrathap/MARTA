import os
import sys
import pprint
import time
import tensorflow as tf

from Utils import save_images
from convert_to_tf_record import DataConvertor
from network import Neotx
import numpy as np
pp = pprint.PrettyPrinter()

flags = tf.app.flags
flags.DEFINE_integer("epoch", 200, "Epoch to train [25]")
flags.DEFINE_float("learning_rate", 0.0002, "Learning rate of for adam [0.0002]")
flags.DEFINE_float("beta1", 0.5, "Momentum term of adam [0.5]")
flags.DEFINE_float("beta2", 0.5, "Momentum term of adam [0.5]")
flags.DEFINE_integer("train_size", 30000, "The size of train images [np.inf]")
flags.DEFINE_integer("batch_size", 64, "The number of batch images [64]")
flags.DEFINE_integer("image_size", 64, "The size of image to use (will be center cropped) [108]")
flags.DEFINE_integer("output_size", 64, "The size of the output images to produce [64]")
flags.DEFINE_integer("sample_size", 64, "The number of sample images [64]")
flags.DEFINE_integer("c_dim", 3, "Dimension of image color. [3]")
flags.DEFINE_integer("sample_step", 500, "The interval of generating sample. [500]")
flags.DEFINE_integer("save_step", 50, "The interval of saveing checkpoints. [500]")
flags.DEFINE_string("dataset", "uc_train_256_data", "The name of dataset [celebA, mnist, lsun]")
flags.DEFINE_string("checkpoint_dir", "/data/checkpoint50", "Directory name to save the checkpoints [checkpoint]")
flags.DEFINE_string("sample_dir", "/data/samples50", "Directory name to save the image samples [samples]")
flags.DEFINE_boolean("is_train", True, "True for training, False for testing [False]")
flags.DEFINE_boolean("is_crop", False, "True for training, False for testing [False]")
flags.DEFINE_boolean("visualize", False, "True for visualizing, False for nothing [False]")
flags.DEFINE_string('dataset_dir', '/data/satellitegpu/', 'Location of data.')
flags.DEFINE_string('dataset_path_train', '/data/images/uc_train_256_data/**.jpg', 'Location of training images data.')
flags.DEFINE_string('dataset_path_test', '/data/images/uc_test_256/**.jpg', 'Location of testing images data.')
flags.DEFINE_string('dataset_storage_location', '/data/neotx', 'Location of image store')
flags.DEFINE_string('dataset_name', 'ucdataset', 'Data set name')

FLAGS = flags.FLAGS


def main(_):
    pp.pprint(flags.FLAGS.__flags)

    if not os.path.exists(FLAGS.checkpoint_dir):
        os.makedirs(FLAGS.checkpoint_dir)
    if not os.path.exists(FLAGS.sample_dir):
        os.makedirs(FLAGS.sample_dir)

    z_dim = 100

    # with tf.device("/gpu:0"): # <-- if you have a GPU machine
    z = tf.placeholder(tf.float32, [FLAGS.batch_size, z_dim], name='z_noise')

    data_convotor = DataConvertor(FLAGS.image_size, FLAGS.dataset_name,
                                  FLAGS.dataset_storage_location, FLAGS.c_dim)

    next_batch, iterator = data_convotor.provide_data(FLAGS.batch_size,  'train')
    real_images = tf.placeholder(tf.float32, [FLAGS.batch_size, FLAGS.output_size,
                                              FLAGS.output_size, FLAGS.c_dim], name='real_images')
    neoxt = Neotx()
    # z --> generator for training
    net_g, g_logits = neoxt.generator(z, is_train=True, reuse=tf.AUTO_REUSE)
    # generated fake images --> discriminator
    net_d, d_logits, feature_fake = neoxt.discriminator(net_g, is_train=True, reuse=tf.AUTO_REUSE)
    # real images --> discriminator
    net_d2, d2_logits, feature_real = neoxt.discriminator(real_images, is_train=True, reuse=True)
    # sample_z --> generator for evaluation, set is_train to False

    net_g2, g2_logits = neoxt.generator(z, is_train=False, reuse=True)
    net_d3, d3_logits, _ = neoxt.discriminator(real_images, is_train=False, reuse=True)

    # cost for updating discriminator and generator
    # discriminator: real images are labelled as 1
    d_loss_real = tf.reduce_mean(tf.nn.sigmoid_cross_entropy_with_logits(logits=d2_logits,
                                                                         labels=tf.ones_like(d2_logits)))
    # real == 1
    # discriminator: images from generator (fake) are labelled as 0
    d_loss_fake = tf.reduce_mean(tf.nn.sigmoid_cross_entropy_with_logits(logits=d_logits,
                                                                         labels=tf.zeros_like(d_logits)))
    # fake == 0
    d_loss = d_loss_real + d_loss_fake
    # generator: try to make the the fake images look real (1)
    g_loss1 = tf.reduce_mean(tf.nn.sigmoid_cross_entropy_with_logits(logits=d_logits,
                                                                     labels=tf.ones_like(d_logits)))
    g_loss2 = tf.reduce_mean(tf.nn.l2_loss(feature_real-feature_fake))/(FLAGS.image_size*FLAGS.image_size)
    g_loss = g_loss1 + g_loss2

    global_step = tf.Variable(0, trainable=False)
    starter_learning_rate = 0.1
    learning_rate = tf.train.exponential_decay(starter_learning_rate, global_step,
                                               1000, FLAGS.learning_rate, staircase=True)

    # optimizers for updating discriminator and generator
    d_optimizer = tf.train.AdamOptimizer(FLAGS.learning_rate, beta1=FLAGS.beta1)
    g_optimizer = tf.train.AdamOptimizer(FLAGS.learning_rate, beta1=FLAGS.beta1)

    extra_update_ops = tf.get_collection(tf.GraphKeys.UPDATE_OPS)
    with tf.control_dependencies(extra_update_ops):
        g_vars = tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES, scope="generator/*")
        d_vars = tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES, scope="discriminator/*")
        d_optim = d_optimizer.minimize(d_loss, var_list=d_vars)
        g_optim = g_optimizer.minimize(g_loss, var_list=g_vars)

    saver = tf.train.Saver(max_to_keep=100)

    with tf.Session() as sess:
        sess.run(tf.global_variables_initializer())

        total_parameters = np.sum([np.prod(v.get_shape().as_list()) for v in tf.trainable_variables()])

        print("Total number of parameters: "+ str(total_parameters))

        ckpt = tf.train.get_checkpoint_state(FLAGS.checkpoint_dir)
        if ckpt and ckpt.model_checkpoint_path:
            saver.restore(sess, ckpt.model_checkpoint_path)
            print("[*] Loading checkpoints ...")
        else:
            print("[*] Loading checkpoints failed ...")

        sample_seed = np.random.uniform(low=-1, high=1, size=(FLAGS.batch_size, z_dim)).astype(np.float32)
        if FLAGS.is_train:
            for epoch in range(FLAGS.epoch):
                iter_counter = 0
                batch_images_for_testing = []
                sess.run(iterator.initializer)
                while True:
                    try:
                        batch_images = sess.run([next_batch])
                        batch_images = np.array(batch_images[0][0], dtype=np.float32)/127.5-1
                        batch_z = np.random.uniform(low=-1, high=1, size=(FLAGS.batch_size, z_dim))\
                            .astype(np.float32)
                        start_time = time.time()

                        for _ in range(1):
                            errD, _ = sess.run([d_loss, d_optim], feed_dict={z: batch_z
                                , real_images: batch_images})
                        for _ in range(2):
                            errG, _ = sess.run([g_loss, g_optim], feed_dict={z: batch_z
                                , real_images: batch_images})
                        print("Epoch: [%2d/%2d] [%4d] time: %4.4f, d_loss: %.8f, g_loss: %.8f" \
                              % (epoch, FLAGS.epoch, iter_counter,
                                 time.time() - start_time, errD, errG))
                        sys.stdout.flush()
                        iter_counter += 1
                        if iter_counter == 1:
                            batch_images_for_testing = batch_images

                    except tf.errors.OutOfRangeError:
                        break

                if np.mod(epoch, 1) == 0:
                    img, errG = sess.run([net_g2, g_loss],
                                         feed_dict={z : sample_seed, real_images: batch_images_for_testing})
                    D, D_, errD = sess.run([net_d3, net_d3, d_loss_real],
                                           feed_dict={real_images: batch_images_for_testing})

                    save_images(img, [8, 8], '{}/train_{:02d}.png'.format(FLAGS.sample_dir, epoch))
                    print("[Sample] d_loss: %.8f, g_loss: %.8f" % (errD, errG))
                    sys.stdout.flush()

                if np.mod(epoch, 5) == 0:
                    print("[*] Saving checkpoints...")
                    save_path = saver.save(sess, FLAGS.checkpoint_dir + '/model', global_step=epoch)
                    print("Model saved in path: %s" % save_path)
                    print("[*] Saving checkpoints SUCCESS!")

def describe_network(sess):
     variables_names = [v.name for v in tf.trainable_variables()]
     values = sess.run(variables_names)
     for k, v in zip(variables_names, values):
        print("Variable: ", k)
        print("Shape: ", v.shape)

if __name__ == '__main__':
    tf.app.run()
