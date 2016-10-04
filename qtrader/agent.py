#!/usr/bin/python
# -*- coding: utf-8 -*-
"""
Implement and run an agent to learn in reinforcement learning framework

@author: ucaiado

Created on 08/18/2016
"""
import random
from environment import Agent, Environment
from simulator import Simulator
import translators
import logging
import sys
import time
from bintrees import FastRBTree
from collections import defaultdict
import numpy as np
import pandas as pd
import pickle
import pprint
import preprocess

# Log finle enabled. global variable
DEBUG = True


# setup logging messages
if DEBUG:
    s_format = '%(asctime)s;%(message)s'
    s_now = time.strftime('%c')
    s_now = s_now.replace('/', '').replace(' ', '_').replace(':', '')
    s_file = 'log/train_test/sim_{}.log'.format(s_now)
    logging.basicConfig(filename=s_file, format=s_format)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.DEBUG)

    formatter = logging.Formatter(s_format)
    ch.setFormatter(formatter)
    root.addHandler(ch)


'''
Begin help functions
'''


class InvalidOptionException(Exception):
    """
    InvalidOptionException is raised by the run() function and indicate that no
    valid test option was selected
    """
    pass

'''
End help functions
'''


class BasicAgent(Agent):
    '''
    A Basic agent representation that learns to drive in the smartcab world.
    '''
    actions_to_open = [None, 'BEST_BID', 'BEST_OFFER', 'BEST_BOTH']
    actions_to_close_when_short = [None, 'BEST_BID']
    actions_to_close_when_long = [None, 'BEST_OFFER']
    actions_to_stop_when_short = [None, 'BEST_BID', 'BUY']
    actions_to_stop_when_long = [None, 'BEST_OFFER', 'SELL']
    FROZEN_POLICY = False

    def __init__(self, env, i_id, f_min_time=3600.):
        '''
        Initiate a BasicAgent object. save all parameters as attributes
        :param env: Environment Object. The Environment where the agent acts
        :param i_id: integer. Agent id
        :param f_min_time: float. Minimum time in seconds to the agent react
        '''
        # sets self.env = env
        super(BasicAgent, self).__init__(env, i_id)
        # Initialize any additional variables here
        self.f_min_time = f_min_time
        self.next_time = 0.
        self.max_pos = 100.
        # self.scaler = preprocess.ClusterScaler()
        self.scaler = preprocess.LessClustersScaler()
        self.s_agent_name = 'BasicAgent'
        self.last_max_pnl = None
        self.f_delta_pnl = 0.  # defined at [-inf, 0)
        self.old_state = None
        self.last_action = None

    def _freeze_policy(self):
        '''
        Freeze agent's policy so it will not update the qtable in simulation
        '''
        self.FROZEN_POLICY = True
        s_print = '{}.freeze_policy(): Policy has been frozen !'
        s_print = s_print.format(self.s_agent_name)
        if DEBUG:
            root.debug(s_print)
        else:
            print s_print

    def reset(self):
        '''
        Reset the state and the agent's memory about its positions
        '''
        self.state = None
        self.position = {'qAsk': 0, 'Ask': 0., 'qBid': 0, 'Bid': 0.}
        self.d_order_tree = {'BID': FastRBTree(), 'ASK': FastRBTree()}
        self.d_order_map = {}
        # Reset any variables here, if required
        self.next_time = 0.

    def should_update(self):
        '''
        Return a boolean informing if it is time to update the agent
        '''
        if self.env.i_nrow < 5:
            return False
        return self.env.order_matching.last_date >= self.next_time
        # return False

    def update(self, msg_env):
        '''
        Update the state of the agent
        :param msg_env: dict. A message generated by the order matching
        '''
        # check if should update, if it is not a trade
        if not msg_env:
            if not self.should_update():
                return None
        # recover basic infos
        inputs = self.env.sense(self)
        state = self.env.agent_states[self]

        # Update state (position ,volume and if has an order in bid or ask)
        self.state = self._get_intern_state(inputs, state)

        # Select action according to the agent's policy
        l_msg = self._take_action(self.state, msg_env)

        # # Execute action and get reward
        # print '\ncurrent action: {}\n'.format(action)
        reward = 0.
        # pprint.pprint(l_msg)
        self.env.update_order_book(l_msg)
        s_action = None
        s_action2 = s_action
        l_prices_to_print = []
        if len(l_msg) == 0:
            reward += self.env.act(self, None)
        for msg in l_msg:
            if msg['agent_id'] == self.i_id:
                s_action = msg['action']
                s_action2 = s_action
                s_indic = msg['agressor_indicator']
                l_prices_to_print.append('{:0.2f}'.format(msg['order_price']))
                if s_indic == 'Agressive' and s_action == 'SELL':
                    s_action2 = 'HIT'  # hit the bid
                elif s_indic == 'Agressive' and s_action == 'BUY':
                    s_action2 = 'TAKE'  # take the offer
                reward += self.env.act(self, msg)
        # NOTE: I am not sure about that, but at least makes sense... I guess
        # I should have to apply the reward to the action that has generated
        # the trade (when my order was hit, I was in the book before)
        if s_action2 == s_action:
            if s_action == 'BUY':
                s_action = 'BEST_BID'
            elif s_action == 'SELL':
                s_action = 'BEST_OFFER'
        # Learn policy based on state, action, reward
        if not self.FROZEN_POLICY:
            # does not update if it is frozen
            self._apply_policy(self.state, s_action, reward)
        # calculate the next time that the agent will react
        self.next_time = self.env.order_matching.last_date
        self.next_time += self.f_min_time

        # print agent inputs
        s_date = self.env.order_matching.row['Date']
        s_rtn = '{}.update(): time = {}, position = {}, inputs = {}, action'
        s_rtn += ' = {}, price_action = {}, pnl = {:0.2f}, delta_pnl = {:0.2f}'
        s_rtn += ', reward = {}'
        inputs['midPrice'] = '{:0.2f}'.format(inputs['midPrice'])
        # inputs['logret'] = '{:0.4f}%'.format(inputs['logret'] * 100)
        # inputs['deltaMid'] = '{:0.3f}'.format(inputs['deltaMid'])
        inputs.pop('deltaMid')
        inputs.pop('logret')
        inputs.pop('qAggr')
        inputs.pop('qTraded')
        inputs['cluster'] = self.state['cluster']
        # check the last maximum pnl considering just the current position
        f_delta_pnl = 0.
        f_pnl = self.env.agent_states[self]['Pnl']
        if self.env.agent_states[self]['Position'] == 0:
            self.last_max_pnl = None
        else:
            self.last_max_pnl = max(self.last_max_pnl,
                                    self.env.agent_states[self]['Pnl'])
            f_delta_pnl = f_pnl - self.last_max_pnl
            self.f_delta_pnl = f_delta_pnl
        # Print inputs and agent state
        if DEBUG:
            root.debug(s_rtn.format(self.s_agent_name,
                                    s_date,
                                    state['Position'],
                                    inputs,
                                    s_action2,
                                    l_prices_to_print,
                                    f_pnl,
                                    f_delta_pnl,
                                    reward))
        else:
            print s_rtn.format(self.s_agent_name,
                               s_date,
                               state['Position'],
                               inputs,
                               s_action2,
                               l_prices_to_print,
                               f_pnl,
                               f_delta_pnl,
                               reward)

    def _get_intern_state(self, inputs, state):
        '''
        Return a dcitionary representing the intern state of the agent
        :param inputs: dictionary. traffic light and presence of cars
        :param state: dictionary. the current position of the agent
        '''
        d_data = {}
        d_data['OFI'] = inputs['qOfi']
        d_data['qBID'] = inputs['qBid']
        d_data['BOOK_RATIO'] = inputs['qBid'] * 1. / inputs['qAsk']
        d_data['LOG_RET'] = inputs['logret']

        i_cluster = self.scaler.transform(d_data)
        d_rtn = {}
        d_rtn['cluster'] = i_cluster
        d_rtn['Position'] = float(state['Position'])
        d_rtn['best_bid'] = state['best_bid']
        d_rtn['best_offer'] = state['best_offer']

        return d_rtn

    def _take_action(self, t_state, msg_env):
        '''
        Return a list of messages according to the agent policy
        :param t_state: tuple. The inputs to be considered by the agent
        :param msg_env: dict. Order matching message
        '''
        # check if have occured a trade
        if msg_env:
            if msg_env['order_status'] in ['Filled', 'Partialy Filled']:
                return [msg_env]
        # select a randon action, but not trade more than the maximum position
        valid_actions = list(self.actions_to_open)
        f_pos = self.position['qBid'] - self.position['qAsk']
        if f_pos <= (self.max_pos * -1):
            valid_actions = list(self.actions_to_close_when_short)  # copy
            if abs(self.f_delta_pnl) >= (4.-1e-6):
                valid_actions = list(self.actions_to_stop_when_short)
        elif f_pos >= self.max_pos:
            valid_actions = list(self.actions_to_close_when_long)
            if abs(self.f_delta_pnl) >= (4.-1e-6):
                valid_actions = list(self.actions_to_stop_when_long)
        # NOTE: I should change just this function when implementing
        # the learning agent
        s_action = self._choose_an_action(t_state, valid_actions)
        # build a list of messages based on the action taken
        l_msg = self._translate_action(t_state, s_action)
        return l_msg

    def _choose_an_action(self, t_state, valid_actions):
        '''
        Return an action from a list of allowed actions according to the
        agent policy
        :param valid_actions: list. List of the allowed actions
        :param t_state: tuple. The inputs to be considered by the agent
        '''
        return random.choice(valid_actions)

    def _translate_action(self, t_state, s_action):
        '''
        Translate the action taken into messaged to environment
        :param t_state: tuple. The inputs to be considered by the agent
        :param s_action: string. The action taken
        '''
        my_ordmatch = self.env.order_matching
        row = my_ordmatch.row.copy()
        idx = self.env.i_nrow
        i_id = self.i_id
        row['Size'] = 100.
        # generate trade
        if s_action == 'BUY':
            row['Type'] = 'TRADE'
            row['Price'] = self.env.best_ask[0]
            return translators.translate_trades(idx,
                                                row,
                                                my_ordmatch,
                                                'ASK',
                                                i_id)
        elif s_action == 'SELL':
            row['Type'] = 'TRADE'
            row['Price'] = self.env.best_bid[0]
            return translators.translate_trades(idx,
                                                row,
                                                my_ordmatch,
                                                'BID',
                                                i_id)
        # generate limit order or cancel everything
        else:
            return translators.translate_to_agent(self,
                                                  s_action,
                                                  my_ordmatch,
                                                  0.01)  # 1 cent inside book
        return []

    def _apply_policy(self, state, action, reward):
        '''
        Learn policy based on state, action, reward
        :param state: dictionary. The current state of the agent
        :param action: string. the action selected at this time
        :param reward: integer. the rewards received due to the action
        '''
        # save current state, action
        self.old_state = state
        self.last_action = action


class BasicLearningAgent(BasicAgent):
    '''
    A representation of an agent that learns using a basic implementation of
    Q-learning that is suited for deterministic Markov decision processes
    '''

    def __init__(self, env, i_id, f_min_time=3600., f_gamma=0.5):
        '''
        Initialize a BasicLearningAgent. Save all parameters as attributes
        :param env: Environment object. The grid-like world
        :*param f_gamma: float. weight of delayed versus immediate rewards
        '''
        # sets self.env = env, state = None, next_waypoint = None
        super(BasicLearningAgent, self).__init__(env=env,
                                                 i_id=i_id,
                                                 f_min_time=f_min_time)
        # Initialize any additional variables here
        self.max_pos = 100.
        self.q_table = defaultdict(lambda: defaultdict(float))
        self.f_gamma = f_gamma
        self.last_reward = None
        self.s_agent_name = 'BasicLearningAgent'

    def _choose_an_action(self, d_state, valid_actions):
        '''
        Return an action from a list of allowed actions according to the
        agent policy
        :param valid_actions: list. List of the allowed actions
        :param d_state: dictionary. The inputs to be considered by the agent
        '''
        # convert position to float (I should correct that somewhere)
        d_state['Position'] = float(d_state['Position'])
        # set a random action in case of exploring world
        max_val = 0.01
        best_Action = random.choice(valid_actions)
        # arg max Q-value choosing a action better than zero
        for action, val in self.q_table[str(d_state)].iteritems():
            # if the agent is positioned, should check just what is allowed
            if action in valid_actions:
                if val > max_val:
                    max_val = val
                    best_Action = action
        if abs(self.position['qBid'] - self.position['qAsk']) > 0:
            if not isinstance(best_Action, type(None)):
                # s_rtn = '\n\n=================\n best action:{}, position:'
                # s_rtn += ' {}, valid actions: {}\n===========\n\n\n'
                # s_rtn = s_rtn.format(best_Action,
                #                      self.position,
                #                      valid_actions)
                # root.debug(s_rtn)
                # raise NotImplementedError
                pass
        return best_Action

    def _apply_policy(self, state, action, reward):
        '''
        Learn policy based on state, action, reward
        :param state: dictionary. The current state of the agent
        :param action: string. the action selected at this time
        :param reward: integer. the rewards received due to the action
        '''
        # check if there is some state in cache
        if self.old_state:
            # apply: Q <- r + y max_a' Q(s', a')
            # note that s' is the result of apply a in s. a' is the action that
            # would maximize the Q-value for the state s'
            s_state = str(state)
            max_Q = 0.
            l_aux = self.q_table[s_state].values()
            if len(l_aux) > 0:
                max_Q = max(l_aux)
            # update qtable
            gamma_f_max_Q_a_prime = self.f_gamma * max_Q
            f_new = self.last_reward + gamma_f_max_Q_a_prime
            self.q_table[str(self.old_state)][self.last_action] = f_new
        # save current state, action and reward to use in the next run
        # apply s <- s'
        self.old_state = state
        self.last_action = action
        self.last_reward = reward
        # make sure that the current state has at least the current reward
        # notice that old_state and last_action is related to the current (s,a)
        # at this point, and not to (s', a'), as previously used
        if not self.q_table[str(self.old_state)][self.last_action]:
            s_aux = str(self.old_state)
            self.q_table[s_aux][self.last_action] = self.last_reward

    def set_qtable(self, s_fname):
        '''
        Set up the q-table to be used in testing simulation and freeze policy
        :param s_fname: string. Path to the qtable to be used
        '''
        # freeze policy
        self._freeze_policy()
        # load qtable and transform in a dictionary
        df_qtable = pd.read_csv(s_fname, sep='\t', index_col=0)
        for s_idx, row in df_qtable.iterrows():
            for s_key, f_val in row.iteritems():
                if not np.isnan(f_val):
                    if s_key == 'Unnamed: 1':
                        s_key = None
                    self.q_table[s_idx][s_key] = f_val
            # fill stop actions to be desirable over any other action
            for s_key in ['BUY', 'SELL']:
                f_val = self.q_table[s_idx][s_key]
                self.q_table[s_idx][s_key] = max(f_val, 0.)
        # log file used
        s_print = '{}.set_qtable(): Setting up the agent to use'
        s_print = s_print.format(self.s_agent_name)
        s_print += ' the qtable at {}'.format(s_fname)
        if DEBUG:
            root.debug(s_print)
        else:
            print s_print


class LearningAgent_k(BasicLearningAgent):
    '''
    A representation of an agent that learns to trade adopting a probabilistic
    approach to select actions
    '''

    def __init__(self, env, i_id, f_min_time=3600., f_gamma=0.5, f_k=0.8):
        '''
        Initialize a LearningAgent_k. Save all parameters as attributes
        :param env: Environment object. The grid-like world
        :*param f_gamma: float. weight of delayed versus immediate rewards
        :*param f_k: float. How strongly should favor high Q-hat values
        '''
        # sets self.env = env, state = None, next_waypoint = None, and a
        # default color
        super(LearningAgent_k, self).__init__(env=env,
                                              i_id=i_id,
                                              f_min_time=f_min_time,
                                              f_gamma=f_gamma)
        # Initialize any additional variables here
        self.f_k = f_k
        self.s_agent_name = 'LearningAgent_k'

    def _choose_an_action(self, t_state, valid_actions):
        '''
        Return an action according to the agent policy
        :param valid_actions: list. List of the allowed actions
        :param t_state: tuple. The inputs to be considered by the agent
        '''
        # set a random action in case of exploring world
        max_val = 0.01
        cum_prob = 1.
        f_count = 0.
        f_prob = 0.
        best_Action = random.choice(valid_actions)
        # if the policy is frozen and the agent didnt observed the state
        # previously, do nothing (or close out its positions)
        if self.FROZEN_POLICY:
            best_Action = None
            if 'BUY' in valid_actions:
                best_Action = 'BUY'
            elif 'SELL' in valid_actions:
                best_Action = 'SELL'
        # arg max Q-value choosing a action better than zero
        for action, val in self.q_table[str(t_state)].iteritems():
            # if the agent is positioned, should check just what is allowed
            if action in valid_actions:
                # force to stop loss action be the last desired
                if action in ['BUY', 'SELL']:
                    val = 0.
                # just consider action with positive rewards
                # due to the possibility to use 0 < k < 1.
                if val >= 0.:
                    f_count += 1.
                    cum_prob += self.f_k ** val
                    if val > max_val:
                        max_val = val
                        best_Action = action
        # if the agent still did not test all actions: (4. - f_count) * 0.15
        f_aux = len(valid_actions) * 1.
        f_prob = ((self.f_k ** max_val) / ((f_aux-f_count) * 0.15 + cum_prob))
        if self.FROZEN_POLICY:
            # always take the best action recorded if the policy is frozen
            f_prob = 1.
        # print 'PROB: {:.2f}'.format(f_prob)
        # choose the best_action just if: eps <= k**thisQhat / sum(k**Qhat)
        if (random.random() <= f_prob):
            s_print = '{}.choose_an_action(): '.format(self.s_agent_name)
            s_print += 'action = explotation, k = {}'.format(self.f_k)
            s_print += ', prob: {:0.2f}'.format(f_prob)
            if DEBUG:
                root.debug(s_print)
            else:
                print s_print
            return best_Action
        else:
            s_print = '{}.choose_an_action(): '.format(self.s_agent_name)
            s_print += 'action = exploration, k = {}'.format(self.f_k)
            s_print += ', prob: {:0.2f}'.format(f_prob)
            if DEBUG:
                root.debug(s_print)
            else:
                print s_print
            return random.choice(valid_actions)


class LearningAgent(LearningAgent_k):
    '''
    A representation of an agent that learns to drive assuming that the world
    is a non-deterministic MDP using Q-learning and adopts a probabilistic
    approach to select actions
    '''

    def __init__(self, env, i_id, f_min_time=3600., f_gamma=0.5, f_k=0.8):
        '''
        Initialize a LearningAgent. Save all parameters as attributes
        :param env: Environment object. The grid-like world
        :*param f_gamma: float. weight of delayed versus immediate rewards
        :*param f_k: float. How strongly should favor high Q-hat values
        '''
        # sets self.env = env, state = None, next_waypoint = None, and a
        # default color
        super(LearningAgent, self).__init__(env=env,
                                            i_id=i_id,
                                            f_min_time=f_min_time,
                                            f_gamma=f_gamma,
                                            f_k=f_k)
        # Initialize any additional variables here
        self.s_agent_name = 'LearningAgent'
        self.nvisits_table = defaultdict(lambda: defaultdict(float))
        # print the parameter of the agent
        # [debug]
        if DEBUG:
            s_rtn = 'LearningAgent.__init__(): gamma = {}, k = {}'
            root.debug(s_rtn.format(self.f_gamma, self.f_k))

    def _apply_policy(self, state, action, reward):
        '''
        Learn policy based on state, action, reward
        :param state: dictionary. The current state of the agent
        :param action: string. the action selected at this time
        :param reward: integer. the rewards received due to the action
        '''
        # count the number of times this (s,a) was reached and the decay factor
        self.nvisits_table[str(self.old_state)][self.last_action] += 1
        f_alpha = self.nvisits_table[str(self.old_state)][self.last_action]
        f_alpha = 1./(1.+f_alpha)
        # f_alpha = 1.
        # check if there is some state in cache
        if self.old_state:
            # apply: Q <- r + y max_a' Q(s', a')
            # note that s' is the result of apply a in s. a' is the action that
            # would maximize the Q-value for the state s'
            s_state = str(state)
            max_Q = 0.
            l_aux = self.q_table[s_state].values()
            if len(l_aux) > 0:
                max_Q = max(l_aux)
            gamma_f_max_Q_a_prime = self.f_gamma * max_Q
            f_Qhat_prime = self.last_reward + gamma_f_max_Q_a_prime
            f_Qhat = self.q_table[str(self.old_state)][self.last_action]
            f_new = (1.-f_alpha) * f_Qhat + f_alpha * f_Qhat_prime
            # apply: Q <- (1-a_n) Q(s,a) + a_n [r + y max_a' Q(s', a')]
            self.q_table[str(self.old_state)][self.last_action] = f_new
        # save current state, action and reward to use in the next run
        # apply s <- s'
        self.old_state = state
        self.last_action = action
        self.last_reward = reward
        # make sure that the current state has at least the current reward
        # notice that old_state and last_action is related to the current (s,a)
        # at this point, and not to (s', a'), as previously used
        if not self.q_table[str(self.old_state)][self.last_action]:
            s_aux = str(self.old_state)
            self.q_table[s_aux][self.last_action] = self.last_reward


def run(s_option):
    """
    Run the agent for a finite number of trials.:
    :param s_option: string. The type of the test
    """
    i_idx = 15  # index of the start file to be used in simulations
    n_trials = 10  # number of repetitions of the same sessions
    n_sessions = 1  # number of different days traded
    # Set up environment
    s_fname = 'data/petr4_0725_0818_2.zip'
    e = Environment(s_fname=s_fname, i_idx=i_idx)
    # create agent
    if s_option in ['train_learner', 'test_learner', 'optimize_k',
                    'optimize_gamma']:
        a = e.create_agent(LearningAgent_k, f_min_time=2., f_k=0.5)
    elif s_option == 'test_random':
        a = e.create_agent(BasicAgent, f_min_time=2.)
    else:
        l_aux = ['train_learner', 'test_learner', 'test_random', 'optimize_k',
                 'optimize_gamma']
        s_err = 'Select an <OPTION> between: \n{}'.format(l_aux)
        raise InvalidOptionException(s_err)
    e.set_primary_agent(a)  # specify agent to track

    # set up the simulation object
    sim = Simulator(e, update_delay=1.00, display=False)

    if 'train' in s_option:
        # ==== IN-SAMPLE TEST ====
        # Training the agent
        s_print = 'run(): Starting training session ! In-Sample Test.'
        if DEBUG:
            root.debug(s_print)
        else:
            print s_print

        # run for a specified number of trials
        sim.train(n_trials=n_trials, n_sessions=n_sessions)

        # test the agent
        s_print = 'run(): Starting testing phase ! In-Sample Test.'
        if DEBUG:
            root.debug(s_print)
        else:
            print s_print
        # run for a specified number of trials. should have the same number of
        # trials and session of the training phase
        sim.in_sample_test(n_trials=n_trials, n_sessions=n_sessions)
    elif s_option in ['test_random', 'test_learner']:
        # ==== OUT-OF-SAMPLE TEST ====
        # test the agent
        s_print = 'run(): Starting testing phase ! Out-of-Sample Test.'
        if DEBUG:
            root.debug(s_print)
        else:
            print s_print
        # run for a specified number of trials
        s_qtable = 'log/qtable/LearningAgent_k_qtable_{}.log'.format(n_trials)
        if e.primary_agent.s_agent_name == 'BasicAgent':
            # run that if is the basicagent
            sim.out_of_sample(s_qtable=s_qtable,
                              n_start=n_sessions+i_idx,
                              n_trials=20,
                              n_sessions=1)
        else:
            # the learning agent, when the policy is freezed, will always take
            # the same actions. So there is no meaning on test multiple times
            sim.out_of_sample(s_qtable=s_qtable,
                              n_start=n_sessions+i_idx,
                              n_trials=1,
                              n_sessions=1)

    elif s_option == 'optimize_k':
        # k tests
        raise NotImplementedError
        for f_k in [0.3, 0.8, 1.3, 2.]:
            e = Environment()
            a = e.create_agent(LearningAgent_k, f_k=f_k)
            e.set_primary_agent(a, enforce_deadline=True)
            sim = Simulator(e, update_delay=0.01, display=False)
            sim.run(n_trials=10)
    elif s_option == 'optimize_gamma':
        # gamma test
        raise NotImplementedError
        for f_gamma in [0.2, 0.5, 0.8, 1.]:
            e = Environment()
            a = e.create_agent(LearningAgent, f_gamma=f_gamma)
            e.set_primary_agent(a, enforce_deadline=True)
            sim = Simulator(e, update_delay=0.01, display=False)
            sim.run(n_trials=10)


if __name__ == '__main__':
    # run the code
    try:
        run(sys.argv[1])
    except IndexError:
        s_err = '\nRun "python qtrader/agent.ty <OPTION>" to simulate'
        s_err += ' the behavior of selected agent.\n'
        l_aux = ['train_learner', 'test_learner', 'test_random', 'optimize_k',
                 'optimize_gamma']
        s_err += 'Select an <OPTION> between: {}'.format(l_aux)
        raise InvalidOptionException(s_err)
