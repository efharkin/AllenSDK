import sys
import os
import numpy as np
import pandas as pd

from allensdk.brain_observatory.behavior.behavior_ophys_session import BehaviorOphysSession
from allensdk.brain_observatory.behavior.behavior_ophys_api.behavior_ophys_nwb_api import BehaviorOphysNwbApi
from allensdk.internal.api.behavior_ophys_api import BehaviorOphysLimsApi

import behavior_project_cache as bpc
from importlib import reload; reload(bpc)

def time_from_last(flash_times, other_times):
    last_other_index = np.searchsorted(a=other_times, v=flash_times) - 1
    time_from_last_other = flash_times - other_times[last_other_index]
    
    # flashes that happened before the other thing happened should return nan
    time_from_last_other[last_other_index==-1]=np.nan
    
    return time_from_last_other

def trace_average(values, timestamps, start_time, stop_time):
    values_this_range = values[((timestamps >= start_time) & (timestamps < stop_time))]
    return values_this_range.mean()

def find_change(image_index, omitted_index):
    change = np.diff(image_index) != 0
    change = np.concatenate([np.array([False]), change]) #First flash not a change
    omitted = image_index == omitted_index
    omitted_inds = np.flatnonzero(omitted)
    change[omitted_inds] = False
    change[omitted_inds + 1] = False #Neither the change to the omitted nor the change back should be counted.
    return change

def get_extended_stimulus_presentations(session):
    
    intermediate_df = session.stimulus_presentations.copy()
    
    lick_times = session.licks['time'].values
    reward_times = session.rewards.index.values
    flash_times = intermediate_df['start_time'].values
    change_times = session.trials['change_time'].values
    change_times = change_times[~np.isnan(change_times)]

    # Time from last other for each flash
    time_from_last_lick = time_from_last(flash_times, lick_times)
    time_from_last_reward = time_from_last(flash_times, reward_times)
    time_from_last_change = time_from_last(flash_times, change_times)
    
    intermediate_df['time_from_last_lick'] = time_from_last_lick
    intermediate_df['time_from_last_reward'] = time_from_last_reward
    intermediate_df['time_from_last_change'] = time_from_last_change
    
    # Was the flash a change flash?
    omitted_index = intermediate_df.groupby('image_name').apply(lambda group: group['image_index'].unique()[0])['omitted']
    changes = find_change(intermediate_df['image_index'], omitted_index)
    omitted = intermediate_df['image_index'] == omitted_index

    intermediate_df['change'] = changes
    intermediate_df['omitted'] = omitted

    # Index of each image block
    changes_including_first = np.copy(changes)
    changes_including_first[0] = True
    change_indices = np.flatnonzero(changes_including_first)
    flash_inds = np.arange(len(intermediate_df))
    block_inds = np.searchsorted(a=change_indices, v=flash_inds, side='right') - 1
    
    intermediate_df['block_index'] = block_inds
    
    # Block repetition number
    blocks_per_image = intermediate_df.groupby('image_name').apply(lambda group: np.unique(group['block_index']))
    block_repetition_number = np.copy(block_inds)

    for image_name, image_blocks in blocks_per_image.iteritems():
        if image_name != 'omitted':
            for ind_block, block_number in enumerate(image_blocks):
                # block_rep_number starts as a copy of block_inds, so we can go write over the index number with the rep number
                block_repetition_number[block_repetition_number == block_number] = ind_block
                
    intermediate_df['image_block_repetition'] = block_repetition_number
                
    # Repeat number within a block
    repeat_number = np.full(len(intermediate_df), np.nan)
    assert intermediate_df.iloc[0].name == 0 # Assuming that the row index starts at zero
    for ind_group, group in intermediate_df.groupby('block_index'):
        repeat = 0
        for ind_row, row in group.iterrows():
            if row['image_name'] != 'omitted':
                repeat_number[ind_row] = repeat 
                repeat += 1
    
    intermediate_df['index_within_block'] = repeat_number

    # Lists of licks/rewards on each flash
    licks_each_flash = intermediate_df.apply(lambda row: lick_times[((lick_times > row['start_time']) & (lick_times < row['start_time']+0.75))], axis=1)
    rewards_each_flash = intermediate_df.apply(lambda row: reward_times[((reward_times > row['start_time']) & (reward_times < row['start_time']+0.75))], axis=1)
    
    intermediate_df['licks_each_flash'] = licks_each_flash
    intermediate_df['rewards_each_flash'] = rewards_each_flash

    # Average running speed on each flash
    flash_running_speed = intermediate_df.apply(lambda row: trace_average(session.running_speed.values,
                                                                  session.running_speed.timestamps,
                                                                  row['start_time'],
                                                                  row['stop_time']), axis=1)
    
    intermediate_df['flash_running_speed'] = flash_running_speed

    
    # Do some tests
    #assert sum(licks_each_flash) == len(session.licks) #something like this
    
    extended_stim_columns = ['time_from_last_lick',
                            'time_from_last_reward',
                            'time_from_last_change',
                            'change', 
                            'omitted',
                            'block_index',
                            'image_block_repetition',
                            'index_within_block',
                            'licks_each_flash',
                             'rewards_each_flash',
                            'flash_running_speed']

    return intermediate_df[extended_stim_columns]    

if __name__=='__main__':

    experiment_id = sys.argv[1]
    cache_json = {'manifest_path': '/allen/programs/braintv/workgroups/nc-ophys/visual_behavior/SWDB_2019/visual_behavior_data_manifest.csv',
                  'nwb_base_dir': '/allen/programs/braintv/workgroups/nc-ophys/visual_behavior/SWDB_2019/nwb_files',
                  'analysis_files_base_dir': '/allen/programs/braintv/workgroups/nc-ophys/visual_behavior/SWDB_2019/extra_files'
                  }

    cache = bpc.BehaviorProjectCache(cache_json)
    # experiment_id = cache.manifest.iloc[5]['ophys_experiment_id']
    nwb_path = cache.get_nwb_filepath(experiment_id)
    api = BehaviorOphysNwbApi(nwb_path)
    session = BehaviorOphysSession(api)

    output_path = '/allen/programs/braintv/workgroups/nc-ophys/visual_behavior/SWDB_2019/extra_files'

    extended_stimulus_presentations_df = get_extended_stimulus_presentations(session)

    output_fn = os.path.join(output_path, 'extended_stimulus_presentations_df_{}.h5'.format(experiment_id))
    print('Writing extended_stimulus_presentations_df to {}'.format(output_fn))
    extended_stimulus_presentations_df.to_hdf(output_fn, key='df')

