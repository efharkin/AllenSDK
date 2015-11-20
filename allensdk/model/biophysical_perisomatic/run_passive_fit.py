import os, sys
import subprocess
import numpy as np
import ephys_utils
import passive_fitting.preprocess as passive_prep

import allensdk.core.json_utilities as ju
from allensdk.model.biophys_sim.config import Config
from allensdk.core.nwb_data_set import NwbDataSet
import allensdk.model.biophysical_perisomatic.passive_fitting.neuron_passive_fit
import logging

def run_passive_fit(description):
    output_directory = description.manifest.get_path('WORKDIR')
    neuronal_model = ju.read(description.manifest.get_path('neuronal_model_data'))
    specimen_data = neuronal_model['specimen']
    
    is_spiny = not any(t['name'] == u'dendrite type - aspiny' for t in specimen_data['specimen_tags'])
    
    all_sweeps = specimen_data['ephys_sweeps']
    if not os.path.exists(output_directory):
        os.makedirs(output_directory)

    cap_check_sweeps, _, _ = \
        ephys_utils.get_sweeps_of_type('C1SQCAPCHK',
                                       all_sweeps)
    
    passive_fit_data = {}

    if len(cap_check_sweeps) > 0:
        data_set = NwbDataSet(description.manifest.get_path('stimulus_path'))
        d = passive_prep.get_passive_fit_data(cap_check_sweeps, data_set);

        grand_up_file = os.path.join(output_directory, 'upbase.dat')
        np.savetxt(grand_up_file, d['grand_up'])

        grand_down_file = os.path.join(output_directory, 'downbase.dat')
        np.savetxt(grand_down_file, d['grand_down'])
        
        passive_fit_data["bridge"] = d['bridge_avg']
        passive_fit_data["escape_time"] = d['escape_t']

        fit_1_file = description.manifest.get_path('fit_1_file')
        fit_1_params = subprocess.check_output([sys.executable,
                                                '-m', allensdk.model.biophysical_perisomatic.passive_fitting.neuron_passive_fit.__name__, 
                                                str(d['escape_t']),
                                                os.path.realpath(description.manifest.get_path('manifest')) ])
        passive_fit_data['fit_1'] = ju.read(fit_1_file)

        fit_2_file = description.manifest.get_path('fit_2_file')
        fit_2_params = subprocess.check_output([sys.executable,
                                                '-m', 'allensdk.model.biophysical_perisomatic.passive_fitting.neuron_passive_fit2',
                                                str(d['escape_t']),
                                                os.path.realpath(description.manifest.get_path('manifest')) ])
        passive_fit_data['fit_2'] = ju.read(fit_2_file)

        fit_3_file = description.manifest.get_path('fit_3_file')
        fit_3_params = subprocess.check_output([sys.executable,
                                                '-m', 'allensdk.model.biophysical_perisomatic.passive_fitting.neuron_passive_fit_elec',
                                                str(d['escape_t']),
                                                str(d['bridge_avg']),
                                                str(1.0),
                                                os.path.realpath(description.manifest.get_path('manifest')) ])
        passive_fit_data['fit_3'] = ju.read(fit_3_file)
        
        # Check for potentially problematic outcomes
        cm_rel_delta = (passive_fit_data["fit_1"]["Cm"] - passive_fit_data["fit_3"]["Cm"]) / passive_fit_data["fit_1"]["Cm"]
        if passive_fit_data["fit_2"]["err"] < passive_fit_data["fit_1"]["err"]:
            Config._log.debug("Fixed Ri gave better results than original")
            print "Fixed Ri gave better results than original"
            if passive_fit_data["fit_2"]["err"] < passive_fit_data["fit_3"]["err"]:
                Config._log.debug("Using fixed Ri results")
                print "Using fixed Ri results"
                passive_fit_data["fit_for_next_step"] = passive_fit_data["fit_2"]
            else:
                Config._log.debug("Using electrode results")
                print "Using electrode results"
                passive_fit_data["fit_for_next_step"] = passive_fit_data["fit_3"]
        elif abs(cm_rel_delta) > 0.1:
            Config._log.debug("Original and electrode fits not in sync:")
            print "Original and electrode fits not in sync:"
            Config._log.debug("original Cm: " + passive_fit_data["fit_1"]["Cm"])
            print "original Cm: ", passive_fit_data["fit_1"]["Cm"]
            Config._log.debug("w/ electrode Cm: " + passive_fit_data["fit_3"]["Cm"])
            print "w/ electrode Cm: ", passive_fit_data["fit_3"]["Cm"]
            if passive_fit_data["fit_1"]["err"] < passive_fit_data["fit_3"]["err"]:
                Config._log.debug("Original has lower error")
                print "Original has lower error"
                passive_fit_data["fit_for_next_step"] = passive_fit_data["fit_1"]
            else:
                Config._log.debug("Electrode has lower error")
                print "Electrode has lower error"
                passive_fit_data["fit_for_next_step"] = passive_fit_data["fit_3"]
        else:
            passive_fit_data["fit_for_next_step"] = passive_fit_data["fit_1"]

        ra = passive_fit_data["fit_for_next_step"]["Ri"]
        if is_spiny:
            combo_cm = passive_fit_data["fit_for_next_step"]["Cm"]
            a1 = passive_fit_data["fit_for_next_step"]["A1"]
            a2 = passive_fit_data["fit_for_next_step"]["A2"]
            cm1 = 1.0
            cm2 = (combo_cm * (a1 + a2) - a1) / a2
        else:
            cm1 = passive_fit_data["fit_for_next_step"]["Cm"]
            cm2 = passive_fit_data["fit_for_next_step"]["Cm"]
    else:
        Config._log.debug("No cap check trace found")
        print "No cap check trace found"
        ra = 100.0
        cm1 = 1.0
        if is_spiny:
            cm2 = 2.0
        else:
            cm2 = 1.0

    passive_fit_data['ra'] = ra
    passive_fit_data['cm1'] = cm1
    passive_fit_data['cm2'] = cm2
    
    return passive_fit_data

def main(limit, manifest_path):
    app_config = Config()
    description = app_config.load(manifest_path)
    
    run_passive_fit(description)

if __name__ == "__main__":
    limit = sys.argv[-2]
    manifest_path = sys.argv[-1]
    
    main(limit, manifest_path)
    
