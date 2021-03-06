import tensorflow as tf
from tensorflow.python.ops.rnn_cell import GRUCell
from tensorflow.python.ops.functional_ops import scan
from tensorflow.python.ops.control_flow_ops import cond
import sys
import numpy as np
import logging

from tracker.dataset.dstc2 import Dstc2

__author__ = 'Petr Belohlavek, Vojtech Hudecek, Josef Valek'


def next_batch(train_set, batch_size):
    b = 0
    while 1:
        dlg = train_set.dialogs[b * batch_size:(b+1) * batch_size, :, :]
        lengths = train_set.turn_lens[b * batch_size:(b+1) * batch_size, :]
        labels = train_set.labels[b * batch_size:(b+1) * batch_size, :]
        mask = train_set.dial_mask[b * batch_size:(b+1) * batch_size, :]
        b += 1
        if dlg.shape[0] < batch_size:
            break
        yield (dlg, lengths, labels, mask)


def main():
    # Config -----------------------------------------------------------------------------------------------------
    learning_rate = 0.005
    batch_size = 16
    epochs = 50
    hidden_state_dim = 200
    embedding_dim = 300
    log_dir = 'log'

    # Data ---------------------------------------------------------------------------------------------------
    data_portion =  2 * batch_size
    train_set = Dstc2('../data/dstc2/data.dstc2.train.json', sample_unk=0.01, first_n=data_portion)
    valid_set = Dstc2('../data/dstc2/data.dstc2.dev.json', first_n=data_portion, sample_unk=0, max_dial_len=train_set.max_dial_len, words_vocab=train_set.words_vocab, labels_vocab=train_set.labels_vocab)
    test_set = Dstc2('../data/dstc2/data.dstc2.test.json', first_n=data_portion, sample_unk=0, max_dial_len=train_set.max_dial_len, words_vocab=train_set.words_vocab, labels_vocab=train_set.labels_vocab)

    vocab_size = len(train_set.words_vocab)
    output_dim = max(np.unique(train_set.labels)) + 1
    n_train_batches = len(train_set.dialogs) // batch_size

    # Model -----------------------------------------------------------------------------------------------------
    logging.info('Creating model')
    input_bt = tf.placeholder('int32', [batch_size, train_set.max_turn_len], name='input')
    turn_lens_b = tf.placeholder('int32', [batch_size], name='turn_lens')
    mask_b = tf.placeholder('int32', [batch_size], name='dial_mask')
    # mask_bT = lengths2mask2d(dial_lens_b, train_set.max_dial_len)
    labels_b = tf.placeholder('int64', [batch_size], name='labels')
    onehot_labels_bo = tf.one_hot(indices=labels_b,
                                  depth=output_dim,
                                  on_value=1.0,
                                  off_value=0.0,
                                  axis=-1)
    is_first_turn = tf.placeholder(tf.bool)
    gru = GRUCell(hidden_state_dim)
    mlp_hidden_layer_dim = 50
    mlp_input2hidden_W = tf.get_variable('in2hid', initializer=tf.random_normal([hidden_state_dim, mlp_hidden_layer_dim]))
    mlp_input2hidden_B = tf.Variable(tf.random_normal([mlp_hidden_layer_dim]))
    mlp_hidden2output_W = tf.get_variable('hid2out', initializer=tf.random_normal([mlp_hidden_layer_dim, output_dim]))
    mlp_hidden2output_B = tf.Variable(tf.random_normal([output_dim]))

    embeddings_we = tf.get_variable('word_embeddings', initializer=tf.random_uniform([vocab_size, embedding_dim], -1.0, 1.0))
    embedded_input_bte = tf.nn.embedding_lookup(embeddings_we, input_bt)
    dialog_state_before_turn = tf.get_variable('dialog_state_before_turn', initializer=tf.zeros([batch_size, hidden_state_dim], dtype='float32'), trainable=False)

    before_state_bh = cond(is_first_turn,
        lambda: gru.zero_state(batch_size, dtype='float32'),
        lambda: dialog_state_before_turn)

    inputs = [tf.squeeze(i, squeeze_dims=[1]) for i in tf.split(1, train_set.max_turn_len, embedded_input_bte)]

    outputs, state_bh = tf.nn.rnn(cell=gru,
            inputs=inputs,
            initial_state=before_state_bh,
            sequence_length=turn_lens_b,
            dtype=tf.float32)

    # state_tbh = scan(fn=lambda last_state_bh, curr_input_bte: gru(curr_input_bte, last_state_bh)[1],
    #                 elems=tf.transpose(embedded_input_bte, perm=[1, 0, 2]),
    #                 initializer=before_state_bh)

    # state_bh = state_tbh[state_tbh.get_shape()[0]-1, :, :]
    dialog_state_before_turn.assign(state_bh)

    projection_ho = tf.get_variable('project2labels',
                                    initializer=tf.random_uniform([hidden_state_dim, output_dim], -1.0, 1.0))


    logits_bo = tf.matmul(state_bh, projection_ho)
    # hidden =  tf.add(tf.matmul(state_bh, mlp_input2hidden_W), mlp_input2hidden_B)
    # logits_bo = tf.add(tf.matmul(hidden, mlp_hidden2output_W), mlp_hidden2output_B
    tf.histogram_summary('logits', logits_bo)

    probabilities_bo = tf.nn.softmax(logits_bo)
    tf.histogram_summary('probabilities', probabilities_bo)

    float_mask_b = tf.cast(mask_b,'float32')
    # loss = tf.matmul(tf.expand_dims(tf.cast(mask_b, 'float32'), 0), tf.nn.softmax_cross_entropy_with_logits(logits_bo, onehot_labels_bo)) / tf.reduce_sum(mask_b)
    loss = tf.reduce_sum(tf.mul(float_mask_b, tf.nn.softmax_cross_entropy_with_logits(logits_bo, onehot_labels_bo))) / tf.reduce_sum(float_mask_b)


    tf.scalar_summary('CCE loss', loss)

    predict_b = tf.argmax(logits_bo, 1)
    correct = tf.cast(tf.equal(predict_b, labels_b), 'float32')
    accuracy = tf.reduce_sum(tf.mul(correct, float_mask_b)) / tf.reduce_sum(float_mask_b)

    tf.scalar_summary('Accuracy', accuracy)
    tb_info = tf.merge_all_summaries()

    # Optimizer  -----------------------------------------------------------------------------------------------------
    logging.info('Creating optimizer')
    optimizer = tf.train.AdamOptimizer(learning_rate)
    logging.info('Creating train_op')
    train_op = optimizer.minimize(loss)
    # Session  -----------------------------------------------------------------------------------------------------
    logging.info('Creating session')
    sess = tf.Session()
    logging.info('Initing variables')
    init = tf.initialize_all_variables()
    logging.info('Running session')
    sess.run(init)

    # TB ---------------------------------------------------------------------------------------------------------
    logging.info('See stats via tensorboard: $ tensorboard --logdir %s', log_dir)
    train_writer = tf.train.SummaryWriter(log_dir, sess.graph)

    # Train ---------------------------------------------------------------------------------------------------------
    train_summary = None
    for e in range(epochs):
        logging.info('------------------------------')
        logging.info('Epoch %d', e)

        total_loss = 0
        total_acc = 0
        batch_count = 0
        for bid, (dialogs_bTt, lengths_bT, labels_bT, masks_bT) in enumerate(next_batch(train_set, batch_size)):
            turn_loss = 0
            turn_acc = 0
            n_turns = 0
            first_run = True
            for (turn_bt, label_b, lengths_b, masks_b) in zip(dialogs_bTt.transpose([1,0,2]), labels_bT.transpose([1,0]), lengths_bT.transpose([1,0]), masks_bT.transpose([1,0])):
                if sum(masks_b) == 0:
                    break
                _, batch_loss, batch_accuracy, train_summary = sess.run([train_op, loss, accuracy, tb_info], feed_dict={input_bt: turn_bt,
                                                                                              turn_lens_b: lengths_b,
                                                                                              mask_b: masks_b,
                                                                                              labels_b: label_b,
                                                                                              is_first_turn:first_run})
                first_run = False
                turn_loss += batch_loss
                turn_acc += batch_accuracy
                n_turns += 1
            total_loss += turn_loss / n_turns
            total_acc += turn_acc / n_turns
            batch_count += 1
            logging.info('Batch %d/%d\r', bid, n_train_batches)

        train_writer.add_summary(train_summary, e)
        logging.info('Average train cost %f', total_loss / batch_count)
        logging.info('Average train accuracy: %f', total_acc / batch_count)

        def monitor_stream(work_set, name):
            total_loss = 0
            total_acc = 0
            n_valid_batches = 0
            for bid, (dialogs_bTt, lengths_bT, labels_bT, masks_bT) in enumerate(next_batch(work_set, batch_size)):
                turn_loss = 0
                turn_acc = 0
                n_turns = 0
                first_run = True
                for (turn_bt, label_b, lengths_b, masks_b) in zip(dialogs_bTt.transpose([1,0,2]), labels_bT.transpose([1,0]), lengths_bT.transpose([1,0]), masks_bT.transpose([1,0])):
                    if sum(masks_b) == 0:
                        break
                    input = np.pad(turn_bt, ((0,0), (0, train_set.max_turn_len-turn_bt.shape[1])), 'constant', constant_values=0) if train_set.max_turn_len > turn_bt.shape[1] else turn_bt
                    predictions, batch_loss, batch_acc, valid_summary = sess.run([predict_b, loss, accuracy, tb_info], feed_dict={input_bt: input,
                                                                    turn_lens_b: lengths_b,
                                                                    labels_b: label_b,
                                                                    mask_b: masks_b,
                                                                    is_first_turn:first_run})
                    turn_loss += batch_loss
                    turn_acc += batch_acc
                    first_run = False
                    n_turns += 1
                total_loss += turn_loss / n_turns
                total_acc += turn_acc / n_turns
                n_valid_batches += 1

            logging.info('%s cost: %f', name, total_loss/n_valid_batches)
            logging.info('%s accuracy: %f', name, total_acc/n_valid_batches)

        monitor_stream(valid_set, 'Valid')
        monitor_stream(test_set, 'Test')


if __name__ == '__main__':
    logging.basicConfig(format='%(asctime)s : %(levelname)s : %(message)s', level=logging.INFO)
    logging.getLogger("tensorflow").setLevel(logging.WARNING)
    logging.info('Start')
    main()
    logging.info('Finished')