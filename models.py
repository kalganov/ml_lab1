import dynet as dy
from sklearn import linear_model
import data
RNN_BUILDER = dy.LSTMBuilder


class SimpleRNNNetwork:
    def __init__(self, rnn_num_of_layers, embeddings_size, state_size):
        self.model = dy.Model()
        
        # the embedding paramaters
        self.embeddings = self.model.add_lookup_parameters((data.VOCAB_SIZE, embeddings_size))
        
        # the rnn
        self.RNN = RNN_BUILDER(rnn_num_of_layers, embeddings_size, state_size, self.model)
        
        # project the rnn output to a vector of VOCAB_SIZE length
        self.output_w = self.model.add_parameters((data.VOCAB_SIZE, state_size))
        self.output_b = self.model.add_parameters((data.VOCAB_SIZE))
    
    def _add_eos(self, string):
        string = string.split() + [data.EOS]
        return [data.char2int[c] for c in string]
    
    # preprocessing function for all inputs (should be overriden for different problems)
    def _preprocess_input(self, string):
        return self._add_eos(string)
    
    # preprocessing function for all outputs (should be overriden for different problems)
    def _preprocess_output(self, string):
        return self._add_eos(string)
    
    def _embed_string(self, string):
        return [self.embeddings[char] for char in string]
    
    def _run_rnn(self, init_state, input_vecs):
        s = init_state
        
        states = s.add_inputs(input_vecs)
        rnn_outputs = [s.output() for s in states]
        return rnn_outputs
    
    def _get_probs(self, rnn_output, output_char):
        output_w = dy.parameter(self.output_w)
        output_b = dy.parameter(self.output_b)
        
        probs = dy.softmax(output_w * rnn_output + output_b)
        return -dy.log(dy.pick(probs, output_char))
    
    def get_loss(self, input_string, output_string):
        input_string = self._preprocess_input(input_string)
        output_string = self._preprocess_output(output_string)
        
        dy.renew_cg()
        
        embedded_string = self._embed_string(input_string)
        rnn_state = self.RNN.initial_state()
        rnn_outputs = self._run_rnn(rnn_state, embedded_string)
        loss = []
        for rnn_output, output_char in zip(rnn_outputs, output_string):
            loss.append(self._get_probs(rnn_output.output(), output_char))
        loss = dy.esum(loss)
        return loss
    
    def _predict(self, rnn_output):
        probs = self._get_probs(rnn_output)
        probs = probs.value()
        predicted_char = data.int2char[probs.index(max(probs))]
        return predicted_char
    
    def generate(self, input_string):
        input_string = self._preprocess_input(input_string)
        
        dy.renew_cg()
        
        embedded_string = self._embed_string(input_string)
        rnn_state = self.RNN.initial_state()
        rnn_outputs = self._run_rnn(rnn_state, embedded_string)
        
        output_string = []
        for rnn_output in rnn_outputs:
            predicted_char = self._predict(rnn_output.output())
            output_string.append(predicted_char)
        output_string = ''.join(output_string)
        return output_string


class EncoderDecoderNetwork(SimpleRNNNetwork):
    def __init__(self, enc_layers, dec_layers, embeddings_size, enc_state_size, dec_state_size):
        self.model = dy.Model()
        
        # the embedding paramaters
        self.embeddings = self.model.add_lookup_parameters((data.VOCAB_SIZE, embeddings_size))
        
        # the rnns
        self.ENC_RNN = RNN_BUILDER(enc_layers, embeddings_size, enc_state_size, self.model)
        self.DEC_RNN = RNN_BUILDER(dec_layers, enc_state_size, dec_state_size, self.model)
        
        # project the rnn output to a vector of VOCAB_SIZE length
        self.output_w = self.model.add_parameters((data.VOCAB_SIZE, dec_state_size))
        self.output_b = self.model.add_parameters((data.VOCAB_SIZE))
    
    def _encode_string(self, embedded_string):
        initial_state = self.ENC_RNN.initial_state()
        
        # run_rnn returns all the hidden state of all the slices of the RNN
        hidden_states = self._run_rnn(initial_state, embedded_string)
        
        return hidden_states
    
    def get_loss(self, input_string, output_string):
        input_string = self._add_eos(input_string)
        output_string = self._add_eos(output_string)
        
        dy.renew_cg()
        
        embedded_string = self._embed_string(input_string)
        # The encoded string is the hidden state of the last slice of the encoder
        encoded_string = self._encode_string(embedded_string)[-1]
        
        rnn_state = self.DEC_RNN.initial_state()
        
        loss = []
        for output_char in output_string:
            rnn_state = rnn_state.add_input(encoded_string)
            loss.append(self._get_probs(rnn_state.output(), output_char))
        loss = dy.esum(loss)
        return loss
    
    
    def generate(self, input_string):
        input_string = self._add_eos(input_string)
        
        dy.renew_cg()
        
        embedded_string = self._embed_string(input_string)
        encoded_string = self._encode_string(embedded_string)[-1]
        
        rnn_state = self.DEC_RNN.initial_state()
        
        output_string = []
        while True:
            rnn_state = rnn_state.add_input(encoded_string)
            predicted_char = self._predict(rnn_state.output())
            output_string.append(predicted_char)
            if predicted_char == data.EOS or len(output_string) > 2*len(input_string):
                break
        output_string = ''.join(output_string)
        return output_string.replace('<EOS>', '')


class AttentionNetwork(EncoderDecoderNetwork):
    def __init__(self, enc_layers, dec_layers, embeddings_size, enc_state_size, dec_state_size):
        EncoderDecoderNetwork.__init__(self, enc_layers, dec_layers, embeddings_size, enc_state_size, dec_state_size)
        
        # attention weights
        self.attention_w1 = self.model.add_parameters((enc_state_size, enc_state_size))
        self.attention_w2 = self.model.add_parameters((enc_state_size, dec_state_size))
        self.attention_v = self.model.add_parameters((1, enc_state_size))
        
        self.enc_state_size = enc_state_size
    
    def _attend(self, input_vectors, state):
        w1 = dy.parameter(self.attention_w1)
        w2 = dy.parameter(self.attention_w2)
        v = dy.parameter(self.attention_v)
        attention_weights = []
        
        w2dt = w2 * state.h()[-1]
        for input_vector in input_vectors:
            attention_weight = v * dy.tanh(w1 * input_vector + w2dt)
            attention_weights.append(attention_weight)
        attention_weights = dy.softmax(dy.concatenate(attention_weights))
        
        output_vectors = dy.esum(
                                 [vector * attention_weight for vector, attention_weight in zip(input_vectors, attention_weights)])
        return output_vectors

    def get_loss(self, input_string, output_string):
        input_string = self._add_eos(input_string)
        output_string = self._add_eos(output_string)
        
        dy.renew_cg()
        
        embedded_string = self._embed_string(input_string)
        encoded_string = self._encode_string(embedded_string)
        
        rnn_state = self.DEC_RNN.initial_state().add_input(dy.vecInput(self.enc_state_size))
        
        loss = []
        for output_char in output_string:
            attended_encoding = self._attend(encoded_string, rnn_state)
            rnn_state = rnn_state.add_input(attended_encoding)
            loss.append(self._get_probs(rnn_state.output(), output_char))
        loss = dy.esum(loss)
        return loss
    
    def generate(self, input_string):
        input_string = self._add_eos(input_string)
        
        dy.renew_cg()
        
        embedded_string = self._embed_string(input_string)
        encoded_string = self._encode_string(embedded_string)
        
        rnn_state = self.DEC_RNN.initial_state().add_input(dy.vecInput(self.enc_state_size))
        
        output_string = []
        while True:
            attended_encoding = self._attend(encoded_string, rnn_state)
            rnn_state = rnn_state.add_input(attended_encoding)
            predicted_char = self._predict(rnn_state.output())
            output_string.append(predicted_char)
            if predicted_char == data.EOS or len(output_string) > 2*len(input_string):
                break
        output_string = ''.join(output_string)
        return output_string.replace('<EOS>', '')
