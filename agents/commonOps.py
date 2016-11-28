import tensorflow as tf
import numpy as np

relu_layer_counter = [0]
conv_layer_counter = [0]
linear_layer_counter = [0]
conditional_linear_layer_counter = [0]

def deepmind_Q(input_state, config, Collection=None):
    normalized = input_state / 256.
    tf.add_to_collection(Collection + "_summaries", tf.histogram_summary(
        "normalized_input", normalized))

    head = add_conv_layer(normalized, channels=32,
                          kernel_size=8, stride=4, Collection=Collection)
    head = add_conv_layer(head, channels=64,
                          kernel_size=4, stride=2, Collection=Collection)
    head = add_conv_layer(head, channels=64,
                          kernel_size=3, stride=1, Collection=Collection)

    h_conv3_shape = head.get_shape().as_list()
    head = tf.reshape(
        head, [-1, h_conv3_shape[1] * h_conv3_shape[2] * h_conv3_shape[3]], name="conv3_flat")

    head = add_relu_layer(head, size=512, Collection=Collection)
    # the last layer is linear without a relu
    Q = add_linear_layer(head, config.action_num, Collection, layer_name="Q")
    tf.histogram_summary("Q", Q, collections=Collection + "_summaries")

    # DQN summary
    for i in range(config.action_num):
        tf.scalar_summary("DQN/action" + str(i),
                                 Q[0, i], collections=["Q_summaries"])
    return Q


def state_to_hidden(input_state, config, Collection=None):
    normalized = input_state / 256.
    tf.add_to_collection(Collection + "_summaries", tf.histogram_summary(
        "normalized_input", normalized))

    head = add_conv_layer(normalized, channels=32,
                          kernel_size=8, stride=4, Collection=Collection)
    head = add_conv_layer(head, channels=64,
                          kernel_size=4, stride=2, Collection=Collection)
    head = add_conv_layer(head, channels=64,
                          kernel_size=3, stride=1, Collection=Collection)

    head_shape = head.get_shape().as_list()
    head = tf.reshape(
        head, [-1, head_shape[1] * head_shape[2] * head_shape[3]], name=head.op.name + "_flat")
    return head

# will be called twice under the same var scope
def hidden_to_Q(hidden_state, config, Collection):
    suffix = ""
    if tf.get_variable_scope().reuse:
        suffix = "_prediction"
    head = add_relu_layer(hidden_state, size=512, Collection=Collection,
                          layer_name="final_relu_layer" + suffix, weight_name="final_linear_Q_W")
    # the last layer is linear without a relu
    Q = add_linear_layer(head, config.action_num, Collection, layer_name="Q"+suffix, weight_name="Q_W")
    tf.add_to_collection(Collection + "_summaries",
                         tf.histogram_summary("Q"+suffix, Q))
    return Q


def hidden_to_r(hidden_state, config, Collection):
    head = add_relu_layer(hidden_state, size=256, layer_name="r_relu1", Collection=Collection)
    # the last layer is linear without a relu
    r = add_linear_layer(head, config.action_num, Collection, layer_name="r_linear2", weight_name="r_W")
    tf.add_to_collection(Collection + "_summaries",
                         tf.histogram_summary("r", r))
    return r


def hidden_to_hidden(hidden_state, action, config, Collection):
    hidden_state_shape = hidden_state.get_shape().as_list()
    action_one_hot = tf.one_hot(
        action, config.action_num, 1., 0., name='action_one_hot')
    head = tf.concat(
        1, [action_one_hot, hidden_state], name="one_hot_concat_state")
    head = add_relu_layer(
        head, size=256, layer_name="prediction_relu1", Collection=Collection)
    head = add_relu_layer(
        head, size=hidden_state_shape[1], layer_name="prediction_hidden", Collection=Collection)
    return head

# -- Training ops --
def dqn_train_op(Q, Y, action, config, Collection):
    with tf.name_scope("loss"):
        # could be done more efficiently with gather_nd or transpose + gather
        action_one_hot = tf.one_hot(
            action, config.action_num, 1., 0., name='action_one_hot')
        Q_acted = tf.reduce_sum(
            Q * action_one_hot, reduction_indices=1, name='DQN_acted')

        loss = tf.reduce_sum(
            clipped_l2(Y, Q_acted), name="loss")

        tf.scalar_summary("losses/loss", loss,
                          collections=[Collection + "_summaries"])
        tf.scalar_summary(
            "main/Y_0", Y[0], collections=[Collection + "_summaries"])
        tf.scalar_summary(
            "main/acted_Q_0", Q_acted[0], collections=[Collection + "_summaries"])

    train_op, grads = build_rmsprop_optimizer(
        loss, config.learning_rate, 0.95, 0.01, 1, "graves_rmsprop")

    for grad, var in grads:
        if grad is not None:
            tf.histogram_summary(var.op.name + '/gradients', grad, name=var.op.name +
                                 '/gradients', collections=[Collection + "_summaries"])
    return train_op


def pdqn_train_op(Q, QT, r, rT, predicted_Q, predicted_QT, action, config, Collection):
    with tf.name_scope("loss"):
        # could be done more efficiently with gather_nd or transpose + gather
        action_one_hot = tf.one_hot(
            action, config.action_num, 1., 0., name='action_one_hot')
        Q_action = tf.reduce_sum(
            Q * action_one_hot, reduction_indices=1, name='DQN_acted')
        r_action = tf.reduce_sum(
            r * action_one_hot, reduction_indices=1, name='DQN_acted')

        Q_loss = tf.reduce_sum(
            clipped_l2(QT, Q_action), name="Q_loss")
        # note that the target is defined over all actions
        prediction_loss = config.alpha / config.action_num * tf.reduce_sum(clipped_l2(
            predicted_Q, predicted_QT, grad_clip=config.alpha), name="future_loss")
        r_loss = config.alpha * tf.reduce_sum(
            clipped_l2(r_action, rT), name="R_loss")

        # maybe add the linear factor 2(DQNR-real_R)(predicted_next_Q-next_Y)
        combined_loss = Q_loss + prediction_loss + r_loss

        tf.scalar_summary(
            "losses/Q", Q_loss, collections=[Collection + "_summaries"])
        tf.scalar_summary(
            "losses/predicted", prediction_loss, collections=[Collection + "_summaries"])
        tf.scalar_summary(
            "losses/r", r_loss, collections=[Collection + "_summaries"])
        tf.scalar_summary(
            "losses/combined", combined_loss, collections=[Collection + "_summaries"])

        tf.scalar_summary(
            "main/QT_0", QT[0], collections=[Collection + "_summaries"])
        tf.scalar_summary(
            "main/Q_0", Q_action[0], collections=[Collection + "_summaries"])
        tf.scalar_summary(
            "main/max_predicted_Q_0", tf.reduce_max(predicted_Q, 1)[0], collections=[Collection + "_summaries"])
        tf.scalar_summary(
            "main/predicted_QT_0", tf.reduce_max(predicted_QT, 1)[0], collections=[Collection + "_summaries"])

        tf.scalar_summary(
            "main/r_0", r_action[0], collections=[Collection + "_summaries"])
        tf.scalar_summary(
            "main/rT_0", rT[0], collections=[Collection + "_summaries"])

    train_op, grads = build_rmsprop_optimizer(
        combined_loss, config.learning_rate, 0.95, 0.01, 1, "graves_rmsprop")

    for grad, var in grads:
        if grad is not None:
            tf.histogram_summary(var.op.name + '/gradients', grad, name=var.op.name +
                                 '/gradients', collections=[Collection + "_summaries"])
    return train_op

# -- Transition function ops --
def hidden_to_hidden_concat(hidden_state, action, action_num, Collection):
    hidden_state_shape = hidden_state.get_shape().as_list()
    action_one_hot = tf.one_hot(
        action, action_num, 1., 0., name='action_one_hot')
    head = tf.concat(
        1, [action_one_hot, hidden_state], name="one_hot_concat_state")
    head = add_relu_layer(
        head, size=256, layer_name="prediction_linear1", Collection=Collection)
    head = add_relu_layer(
        head, size=hidden_state_shape[1], layer_name="prediction_linear2", Collection=Collection)
    return head


def hidden_to_hidden_expanded_concat(hidden_state, action, action_num, Collection):
    hidden_state_shape = hidden_state.get_shape().as_list()

    w = get_var("action_embeddings_W", [action_num, 256], initializer=tf.truncated_normal_initializer(
        stddev=0.1), Collection=Collection)
    action_embedding = tf.gather(w, action)

    head = tf.concat(
        1, [action_embedding, hidden_state], name="hidden-action_embedding")
    head = add_relu_layer(
        head, size=256, layer_name="prediction_linear1", Collection=Collection)
    head = add_relu_layer(
        head, size=hidden_state_shape[1], layer_name="prediction_linear2", Collection=Collection)
    return head


def hidden_to_hidden_conditional(hidden_state, action, action_num, Collection):
    hidden_state_shape = hidden_state.get_shape().as_list()

    head = add_conditional_relu_layer(
        hidden_state, action, action_num, size=256, Collection=Collection)
    head = add_relu_layer(
        head, size=hidden_state_shape[1], Collection=Collection)
    return head


# -- Primitive ops --
def build_activation_summary(x, Collection):
    tensor_name = x.op.name
    hs = tf.histogram_summary(tensor_name + '/activations', x)
    ss = tf.scalar_summary(tensor_name + '/sparsity', tf.nn.zero_fraction(x))
    tf.add_to_collection(Collection + "_summaries", hs)
    tf.add_to_collection(Collection + "_summaries", ss)


def conv2d(x, W, stride, name):
    return tf.nn.conv2d(x, W, strides=[1, stride, stride, 1], padding="VALID", name=name)


def xavier_std(in_size, out_size):
    return np.sqrt(2. / (in_size + out_size))


def get_var(name, size, initializer, Collection):
    w = tf.get_variable(name, size, initializer=initializer,
                        collections=[Collection + "_weights", tf.GraphKeys.VARIABLES])
    if tf.get_variable_scope().reuse == False:
        tf.add_to_collection(Collection + "_summaries",
                             tf.histogram_summary(w.op.name, w))
    return w


def add_conv_layer(head, channels, kernel_size, stride, Collection):
    assert len(head.get_shape()
               ) == 4, "can't add a conv layer to this input"
    layer_name = "conv" + \
        str(len(tf.get_collection(Collection + "_convolutions")))
    tf.add_to_collection(Collection + "_convolutions", layer_name)
    head_channels = head.get_shape().as_list()[3]
    w_size = [kernel_size, kernel_size, head_channels, channels]
    std = xavier_std(head_channels * kernel_size **
                     2, channels * kernel_size**2)

    w = get_var(layer_name + "_W", w_size, initializer=tf.truncated_normal_initializer(
        stddev=std), Collection=Collection)

    new_head = tf.nn.relu(
        conv2d(head, w, stride, name=layer_name), name=layer_name + "_relu")
    build_activation_summary(new_head, Collection + "_summaries")
    return new_head


def add_linear_layer(head, size, Collection, layer_name=None, weight_name=None):
    assert len(head.get_shape()
               ) == 2, "can't add a linear layer to this input"
    if layer_name == None:
        layer_name = "linear" + \
            str(len(tf.get_collection(Collection + "_linears")))
        tf.add_to_collection(Collection + "_linears", layer_name)
    if weight_name == None:
        weight_name = layer_name + "_W"
    head_size = head.get_shape().as_list()[1]
    w_size = [head_size, size]
    std = xavier_std(head_size, size)

    w = get_var(weight_name, w_size, initializer=tf.truncated_normal_initializer(
        stddev=std), Collection=Collection)

    new_head = tf.matmul(head, w, name=layer_name)
    build_activation_summary(new_head, Collection + "_summaries")
    return new_head


def add_relu_layer(head, size, Collection, layer_name=None, weight_name=None):
    if layer_name == None:
        layer_name = "relu" + \
            str(len(tf.get_collection(Collection + "_relus")))
        tf.add_to_collection(Collection + "_relus", layer_name)
    head = add_linear_layer(
        head, size, Collection, layer_name, weight_name)
    new_head = tf.nn.relu(head, name=layer_name + "_relu")
    build_activation_summary(new_head, Collection + "_summaries")
    return new_head


def add_conditional_relu_layer(head, condition, condition_size, size, Collection, layer_name=None, weight_name=None):
    assert len(head.get_shape()
               ) == 2, "can't add a linear layer to this input"
    if layer_name == None:
        layer_name = "conditional_relu" + \
            str(len(tf.get_collection(Collection + "_conditional_relus")))
        tf.add_to_collection(Collection + "_conditional_relus", layer_name)
    if weight_name == None:
        weight_name = layer_name + "_W"
    head_size = head.get_shape().as_list()[1]
    w_size = [condition_size, head_size, size]
    std = xavier_std(head_size, size)

    w = get_var(weight_name, w_size, initializer=tf.truncated_normal_initializer(
        stddev=std), Collection=Collection)

    dynamic_batch_size = tf.shape(condition)[0]
    condition_oh = tf.expand_dims(tf.one_hot(
        condition, condition_size, 1., 0.), 1)
    tiled_w = tf.tile(w, [dynamic_batch_size, 1, 1])
    tiled_shape = tiled_w.get_shape()
    tiled_reshaped = tf.reshape(tiled_w, tf.pack(
        [dynamic_batch_size, condition_size, tiled_shape[1] * tiled_shape[2]]))
    conditional_w = tf.batch_matmul(condition_oh, tiled_reshaped)
    conditional_w = tf.reshape(conditional_w, tf.pack(
        [dynamic_batch_size, tiled_shape[1], tiled_shape[2]]))
    new_head = tf.nn.relu(
        tf.batch_matmul(tf.expand_dims(head, 1), conditional_w, name=layer_name), name=layer_name + "_relu")

    new_head = tf.squeeze(new_head, [1])
    build_activation_summary(new_head, Collection + "_summaries")
    return new_head


def clipped_l2(y, y_t, grad_clip=1):
    with tf.name_scope("clipped_l2"):
        batch_delta = y - y_t
        batch_delta_abs = tf.abs(batch_delta)
        batch_delta_quadratic = tf.minimum(batch_delta_abs, grad_clip)
        batch_delta_linear = (
            batch_delta_abs - batch_delta_quadratic) * grad_clip
        batch = batch_delta_linear + batch_delta_quadratic**2 / 2
    return batch


def build_rmsprop_optimizer(loss, learning_rate, rmsprop_decay, rmsprop_constant, gradient_clip, version):
    with tf.name_scope('rmsprop'):
        optimizer = None
        if version == 'rmsprop':
            optimizer = tf.train.RMSPropOptimizer(
                learning_rate, decay=rmsprop_decay, momentum=0.0, epsilon=rmsprop_constant)
        elif version == 'graves_rmsprop':
            optimizer = tf.train.GradientDescentOptimizer(learning_rate)

        grads_and_vars = optimizer.compute_gradients(loss)

        grads = []
        params = []
        for p in grads_and_vars:
            if p[0] == None:
                continue
            grads.append(p[0])
            params.append(p[1])
        #grads = [gv[0] for gv in grads_and_vars]
        #params = [gv[1] for gv in grads_and_vars]
        if gradient_clip > 0:
            grads = tf.clip_by_global_norm(grads, gradient_clip)[0]

        if version == 'rmsprop':
            return optimizer.apply_gradients(zip(grads, params))
        elif version == 'graves_rmsprop':
            square_grads = [tf.square(grad) for grad in grads]

            avg_grads = [tf.Variable(tf.zeros(var.get_shape()))
                         for var in params]
            avg_square_grads = [tf.Variable(
                tf.zeros(var.get_shape())) for var in params]

            update_avg_grads = [grad_pair[0].assign((rmsprop_decay * grad_pair[0]) + tf.scalar_mul((1 - rmsprop_decay), grad_pair[1]))
                                for grad_pair in zip(avg_grads, grads)]
            update_avg_square_grads = [grad_pair[0].assign((rmsprop_decay * grad_pair[0]) + ((1 - rmsprop_decay) * tf.square(grad_pair[1])))
                                       for grad_pair in zip(avg_square_grads, grads)]
            avg_grad_updates = update_avg_grads + update_avg_square_grads

            rms = [tf.sqrt(avg_grad_pair[1] - tf.square(avg_grad_pair[0]) + rmsprop_constant)
                   for avg_grad_pair in zip(avg_grads, avg_square_grads)]

            rms_updates = [grad_rms_pair[0] / grad_rms_pair[1]
                           for grad_rms_pair in zip(grads, rms)]
            train = optimizer.apply_gradients(zip(rms_updates, params))

            return tf.group(train, tf.group(*avg_grad_updates)), grads_and_vars
