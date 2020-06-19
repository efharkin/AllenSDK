# Allen Institute Software License - This software license is the 2-clause BSD
# license plus a third clause that prohibits redistribution for commercial
# purposes without further permission.
#
# Copyright 2015-2017. Allen Institute. All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice,
# this list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright notice,
# this list of conditions and the following disclaimer in the documentation
# and/or other materials provided with the distribution.
#
# 3. Redistributions for commercial purposes are not permitted without the
# Allen Institute's written permission.
# For purposes of this license, commercial purposes is the incorporation of the
# Allen Institute's software into anything for which you will charge fees or
# other compensation. Contact terms@alleninstitute.org for commercial licensing
# opportunities.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.
#
from abc import ABC, abstractmethod
import logging
import os
from ..biophys_sim.neuron.hoc_utils import HocUtils
from allensdk.core.nwb_data_set import NwbDataSet
from fractions import gcd
from skimage.measure import block_reduce
import scipy.interpolate
import numpy as np
import pandas as pd
import h5py
from pkg_resources import resource_filename #@UnresolvedImport

PERISOMATIC_TYPE = "Biophysical - perisomatic"
ALL_ACTIVE_TYPE = "Biophysical - all active"


def create_utils(description, model_type=None):
    ''' Factory method to create a Utils subclass.

    Parameters
    ----------
    description : Config instance
        used to initialize Utils subclass

    model_type : string
        Must be one of [PERISOMATIC_TYPE, ALL_ACTIVE_TYPE].  If none, defaults to PERISOMATIC_TYPE

    Returns
    -------
    Utils instance
    '''



    if model_type is None:
        try:
            model_type = description.data['biophys'][0]['model_type']
        except KeyError as e:
            logging.error("Could not infer model type from description")

    axon_type = description.data['biophys'][1]['axon_type']

    if model_type == PERISOMATIC_TYPE:
        return Utils(description)
    elif model_type == ALL_ACTIVE_TYPE:
        return AllActiveUtils(description, axon_type)


class Utils(HocUtils):
    '''A helper class for NEURON functionality needed for
    biophysical simulations.

    Attributes
    ----------
    h : object
        The NEURON hoc object.
    nrn : object
        The NEURON python object.
    neuron : module
        The NEURON module.
    '''

    _log = logging.getLogger(__name__)

    def __init__(self, description):
        self.update_default_cell_hoc(description)

        super(Utils, self).__init__(description)
        self.stim = None
        self.stim_curr = None
        self.simulation_sampling_rate = None
        self.stimulus_sampling_rate = None

        self.stim_vec_list = []


    def update_default_cell_hoc(self, description, default_cell_hoc='cell.hoc'):
        ''' replace the default 'cell.hoc' path in the manifest with 'cell.hoc' packaged
        within AllenSDK if it does not exist '''

        hoc_files = description.data['neuron'][0]['hoc']
        try:
            hfi = hoc_files.index(default_cell_hoc)

            if not os.path.exists(default_cell_hoc):
                abspath_ch = resource_filename(__name__,
                                               default_cell_hoc)
                hoc_files[hfi] = abspath_ch

                if not os.path.exists(abspath_ch):
                    raise IOError("cell.hoc does not exist!")

                self._log.warning("Using cell.hoc from the following location: %s", abspath_ch)
        except ValueError as e:
            pass


    def generate_morphology(self, morph_filename):
        '''Load a swc-format cell morphology file.

        Parameters
        ----------
        morph_filename : string
            Path to swc.
        '''
        h = self.h

        swc = self.h.Import3d_SWC_read()
        swc.input(morph_filename)
        imprt = self.h.Import3d_GUI(swc, 0)

        h("objref this")
        imprt.instantiate(h.this)

        h("soma[0] area(0.5)")
        for sec in h.allsec():
            sec.nseg = 1 + 2 * int(sec.L / 40.0)
            if sec.name()[:4] == "axon":
                h.delete_section(sec=sec)
        h('create axon[2]')
        for sec in h.axon:
            sec.L = 30
            sec.diam = 1
            sec.nseg = 1 + 2 * int(sec.L / 40.0)
        h.axon[0].connect(h.soma[0], 0.5, 0.0)
        h.axon[1].connect(h.axon[0], 1.0, 0.0)

        h.define_shape()

    def load_cell_parameters(self):
        '''Configure a neuron after the cell morphology has been loaded.'''
        passive = self.description.data['passive'][0]
        genome = self.description.data['genome']
        conditions = self.description.data['conditions'][0]
        h = self.h

        h("access soma")

        # Set fixed passive properties
        for sec in h.allsec():
            sec.Ra = passive['ra']
            sec.insert('pas')
            for seg in sec:
                seg.pas.e = passive["e_pas"]

        for c in passive["cm"]:
            h('forsec "' + c["section"] + '" { cm = %g }' % c["cm"])

        # Insert channels and set parameters
        for p in genome:
            if p["section"] == "glob":  # global parameter
                h(p["name"] + " = %g " % p["value"])
            else:
                if p["mechanism"] != "":
                    h('forsec "' + p["section"] +
                      '" { insert ' + p["mechanism"] + ' }')
                h('forsec "' + p["section"] +
                  '" { ' + p["name"] + ' = %g }' % p["value"])

        # Set reversal potentials
        for erev in conditions['erev']:
            h('forsec "' + erev["section"] + '" { ek = %g }' % erev["ek"])
            h('forsec "' + erev["section"] + '" { ena = %g }' % erev["ena"])

    def setup_iclamp(self,
                     stimulus_path,
                     sweep=0):
        '''Assign a current waveform as input stimulus.

        Parameters
        ----------
        stimulus_path : string
            NWB file name
        '''
        self.stim = self.h.IClamp(self.h.soma[0](0.5))
        self.stim.amp = 0
        self.stim.delay = 0
        # just set to be really big; doesn't need to match the waveform
        self.stim.dur = 1e12

        self.read_stimulus(stimulus_path, sweep=sweep)

        # NEURON's dt is in milliseconds
        simulation_dt = 1.0e3 / self.simulation_sampling_rate
        stimulus_dt = 1.0e3 / self.stimulus_sampling_rate
        self._log.debug("Using simulation dt %f, stimulus dt %f", simulation_dt, stimulus_dt)

        self.h.dt = simulation_dt
        stim_vec = self.h.Vector(self.stim_curr)
        stim_vec.play(self.stim._ref_amp, stimulus_dt)

        stimulus_stop_index = len(self.stim_curr) - 1
        self.h.tstop = stimulus_stop_index * stimulus_dt
        self.stim_vec_list.append(stim_vec)

    def read_stimulus(self, stimulus_path, sweep=0):
        '''Load current values for a specific experiment sweep and setup simulation
        and stimulus sampling rates.

        NOTE: NEURON only allows simulation timestamps of multiples of 40KHz.  To
        avoid aliasing, we set the simulation sampling rate to the least common
        multiple of the stimulus sampling rate and 40KHz.

        Parameters
        ----------
        stimulus path : string
            NWB file name
        sweep : integer, optional
            sweep index
        '''
        Utils._log.info(
            "reading stimulus path: %s, sweep %s",
            stimulus_path,
            sweep)

        stimulus_data = NwbDataSet(stimulus_path)
        sweep_data = stimulus_data.get_sweep(sweep)

        # convert to nA for NEURON
        self.stim_curr = sweep_data['stimulus'] * 1.0e9

        # convert from Hz
        hz = int(sweep_data['sampling_rate'])
        neuron_hz = Utils.nearest_neuron_sampling_rate(hz)

        self.simulation_sampling_rate = neuron_hz
        self.stimulus_sampling_rate = hz

        if hz != neuron_hz:
            Utils._log.debug("changing sampling rate from %d to %d to avoid NEURON aliasing", hz, neuron_hz)

    def record_values(self, compartments='soma'):
        '''Set up output voltage recording.'''
        if compartments == 'all':
            names = []
            parent_names = []
            vecs = []
            for sec in self.h.allsec():
                names.append(sec.name())
                if sec.parentseg() is not None:
                    parent_names.append(sec.parentseg().sec.name())
                else:
                    parent_names.append(None)

                v_vec = self.h.Vector()
                v_vec.record(sec(0.5)._ref_v)
                vecs.append({'v': v_vec})

        elif compartments == 'soma':
            names = [self.h.soma[0].name()]
            parent_names = []
            if self.h.soma[0].parentseg() is not None:
                # This probably shouldn't happen because soma is usually the root
                parent_names.append(self.h.soma[0].parentseg().sec.name())
            else:
                parent_names.append(None)

            v_vec = self.h.Vector()
            v_vec.record(self.h.soma[0](0.5)._ref_v)
            vecs = [{'v': v_vec}]

        else:
            raise NotImplementedError(
                'Expected argument `compartments` to be `all` or `soma`, '
                'got {}'.format(compartments)
            )

        t_vec = self.h.Vector()
        t_vec.record(self.h._ref_t)

        return MorphologicalRecording(
            t_vec, HocMorphology(names, parent_names), vecs
        )

    def get_recorded_voltages(self, recording):
        '''Extract recorded voltages and timestamps given the NeuronRecording instance.
        If self.stimulus_sampling_rate is smaller than self.simulation_sampling_rate,
        resample to self.stimulus_sampling_rate.

        Parameters
        ----------
        recording : NeuronRecording
           constructed by self.record_values

        Returns
        -------
        dict with three keys:
            'v' = (sections, timesteps) numpy.ndarray with voltages
            't' = (timesteps,) numpy.ndarray with timestamps
            'section_names' = iterable of section names

        '''
        junction_potential = self.description.data['fitting'][0]['junction_potential']

        voltages = np.array([sec['v'] for sec in recording.sections()])
        assert voltages.ndim == 2
        t = np.asarray(recording.time)
        assert np.ndim(t) == 1
        names = np.asarray(recording.section_names)

        if self.stimulus_sampling_rate < self.simulation_sampling_rate:
            factor = self.simulation_sampling_rate / self.stimulus_sampling_rate

            Utils._log.debug("subsampling recorded traces by %dX", factor)
            voltages = block_reduce(voltages, (1, factor), np.mean)[:, :len(self.stim_curr)]
            t = block_reduce(t, (factor,), np.min)[:len(self.stim_curr)]

        mV = 1.0e-3
        voltages = (voltages - junction_potential) * mV

        return {"v": voltages, "t": t, "section_names": names}

    @staticmethod
    def nearest_neuron_sampling_rate(hz, target_hz=40000):
        div = gcd(hz, target_hz)
        new_hz = hz * target_hz / div
        return new_hz


class AllActiveUtils(Utils):

    def __init__(self, description, axon_type):
        """
        Parameters
        ----------
        description : Config
            Configuration to run the simulation
        axon_type : string
                truncated - diameter of the axon segments is read from .swc (default)
                stub - diameter of axon segments is 1 micron
            How the axon is replaced within NEURON

        """
        super(AllActiveUtils, self).__init__(description)
        self.axon_type = axon_type

    def generate_morphology(self, morph_filename):
        '''Load a neurolucida or swc-format cell morphology file.

        Parameters
        ----------
        morph_filename : string
            Path to morphology.
        '''
        if self.axon_type == 'stub':
            self._log.info('Replacing axon with a stub : length 60 micron, diameter 1 micron')
            super(AllActiveUtils, self).generate_morphology(morph_filename)
            return

        self._log.info('Legacy model - Truncating reconstructed axon after 60 micron')
        morph_basename = os.path.basename(morph_filename)
        morph_extension = morph_basename.split('.')[-1]
        if morph_extension.lower() == 'swc':
            morph = self.h.Import3d_SWC_read()
        elif morph_extension.lower() == 'asc':
            morph = self.h.Import3d_Neurolucida3()
        else:
            raise Exception("Unknown filetype: %s" % morph_extension)

        morph.input(morph_filename)
        imprt = self.h.Import3d_GUI(morph, 0)

        self.h("objref this")
        imprt.instantiate(self.h.this)

        for sec in self.h.allsec():
            sec.nseg = 1 + 2 * int(sec.L / 40.0)

        self.h("soma[0] area(0.5)")
        axon_diams = [self.h.axon[0].diam, self.h.axon[0].diam]
        self.h.distance(sec=self.h.soma[0])
        for sec in self.h.allsec():
            if sec.name()[:4] == "axon":
                if self.h.distance(0.5, sec=sec) > 60:
                    axon_diams[1] = sec.diam
                    break
        for sec in self.h.allsec():
            if sec.name()[:4] == "axon":
                self.h.delete_section(sec=sec)
        self.h('create axon[2]')
        for index, sec in enumerate(self.h.axon):
            sec.L = 30
            sec.diam = axon_diams[index]

        for sec in self.h.allsec():
            sec.nseg = 1 + 2 * int(sec.L / 40.0)

        self.h.axon[0].connect(self.h.soma[0], 1.0, 0.0)
        self.h.axon[1].connect(self.h.axon[0], 1.0, 0.0)

        # make sure diam reflects 3d points
        self.h.area(.5, sec=self.h.soma[0])

    def load_cell_parameters(self):
        '''Configure a neuron after the cell morphology has been loaded.'''
        passive = self.description.data['passive'][0]
        genome = self.description.data['genome']
        conditions = self.description.data['conditions'][0]
        h = self.h

        h("access soma")

        # Set fixed passive properties
        for sec in h.allsec():
            sec.Ra = passive['ra']
            sec.insert('pas')
            # for seg in sec:
            #     seg.pas.e = passive["e_pas"]

        # for c in passive["cm"]:
        #     h('forsec "' + c["section"] + '" { cm = %g }' % c["cm"])

        # Insert channels and set parameters
        for p in genome:
            section_array = p["section"]
            mechanism = p["mechanism"]
            param_name = p["name"]
            param_value = float(p["value"])
            if section_array == "glob":  # global parameter
                h(p["name"] + " = %g " % p["value"])
            else:
                if hasattr(h, section_array):
                    if mechanism != "":
                        print('Adding mechanism %s to %s'
                              % (mechanism, section_array))
                        for section in getattr(h, section_array):
                            if self.h.ismembrane(str(mechanism),
                                                 sec=section) != 1:
                                section.insert(mechanism)

                    print('Setting %s to %.6g in %s'
                          % (param_name, param_value, section_array))
                    for section in getattr(h, section_array):
                        setattr(section, param_name, param_value)

        # Set reversal potentials
        for erev in conditions['erev']:
            erev_section_array = erev["section"]
            ek = float(erev["ek"])
            ena = float(erev["ena"])

            print('Setting ek to %.6g and ena to %.6g in %s'
                  % (ek, ena, erev_section_array))

            if hasattr(h, erev_section_array):
                for section in getattr(h, erev_section_array):
                    if self.h.ismembrane("k_ion", sec=section) == 1:
                        setattr(section, 'ek', ek)

                    if self.h.ismembrane("na_ion", sec=section) == 1:
                        setattr(section, 'ena', ena)
            else:
                print("Warning: can't set erev for %s, "
                      "section array doesn't exist" % erev_section_array)

        self.h.v_init = conditions['v_init']
        self.h.celsius = conditions['celsius']


class H5Saveable(ABC):
    """Abstract base class for objects that can be save to HDF5 files."""

    def save(self, file_or_h5group):
        """Save to an HDF5 file or h5py.Group."""
        if isinstance(file_or_h5group, h5py.Group):
            self._save_to_h5group(file_or_h5group)
        elif isinstance(file_or_h5group, str):
            with h5py.File(file_or_h5group, 'w') as f:
                self._save_to_h5group(f)
                f.close()
        else:
            raise TypeError(
                'Expected a str name of file or an h5py.Group, '
                'got {} instead'.format(type(file_or_h5group))
            )

    @abstractmethod
    def _save_to_h5group(self, h5group):
        NotImplemented

    @staticmethod
    def load(file_or_h5group):
        """Load from an HDF5 file or h5py.Group."""
        if isinstance(file_or_h5group, h5py.Group):
            return HocMorphology._load_from_h5group(file_or_h5group)
        elif isinstance(file_or_h5group, str):
            with h5py.File(file_or_h5group, 'r') as f:
                loaded = HocMorphology._load_from_h5group(f)
                f.close()
            return loaded
        else:
            raise TypeError(
                'Expected a str name of file or an h5py.Group, '
                'got {} instead'.format(type(file_or_h5group))
            )

    @staticmethod
    @abstractmethod
    def _load_from_h5group(h5group):
        NotImplemented


class MorphologicalRecording:
    # TODO: Inherit from H5Saveable
    def __init__(self, time, morphology, section_vectors):
        assert len(morphology) == len(section_vectors)
        self.morphology = morphology
        self._sections = pd.DataFrame(
            section_vectors, index=morphology.section_names
        )
        self.time = time

    @property
    def section_names(self):
        return self._sections.index.to_numpy()

    def sections(self):
        """Iterator over sections."""
        for section_name in self.section_names:
            yield self.get_section_by_name(section_name)

    def get_section_by_name(self, section_name):
        try:
            section = self._sections.loc[section_name, :]
            section["name"] = section_name
            return section
        except KeyError:
            raise KeyError(
                'NeuronRecording does not have a section called {}'.format(
                    section_name
                )
            )


class HocMorphology(H5Saveable):
    """Reduced neural tree morphology used by NEURON."""

    def __init__(self, section_names, parent_names):
        assert len(section_names) == len(parent_names)
        self._sections = pd.Series(
            parent_names, index=section_names, name='parent'
        )

    def __len__(self):
        return len(self._sections)

    @property
    def section_names(self):
        """Names of all sections in this neuron."""
        return self._sections.index.to_numpy()

    def sections(self):
        """Iterator over sections."""
        for section in self.section_names:
            yield {
                'section_name': str(section),
                'parent_name': str(self.get_parent_by_name(section))
            }

    def get_parent_by_name(self, section_name, return_type='name'):
        self._validate_return_type(return_type)
        try:
            parent = self._sections.loc[section_name]
            if return_type == 'name':
                return str(parent)
            elif return_type == 'index':
                return self._name_to_index(parent)
            else:
                raise RuntimeError
        except KeyError:
            raise KeyError(
                'HocMorphology does not have a section called {}'.format(
                    section_name
                )
            )

    def get_children_by_name(self, section_name, return_type='name'):
        self._validate_return_type(return_type)

        # Get the names of all children.
        child_mask = self._sections == section_name
        child_names = self._sections.index[child_mask].to_numpy()

        # Return child names or indices, depending on `return_type` argument
        if return_type == 'name':
            result = child_names
        elif return_type == 'index':
            result = np.array(
                [self._name_to_index(name) for name in child_names]
            )
        else:
            raise RuntimeError

        return result

    @staticmethod
    def _validate_return_type(return_type):
        if return_type not in ['name', 'index']:
            raise ValueError(
                'Expected `return_type` to be `name` or `index`, got {} '
                'instead.'.format(return_type)
            )

    def _name_to_index(self, section_name):
        matching_rows = np.where(self._sections.index == section_name)[0]

        if len(matching_rows) == 1:
            return matching_rows[0]
        elif len(matching_rows) == 0:
            raise KeyError('Section {} not found'.format(section_name))
        else:
            raise RuntimeError(
                'Found section {} more than once!'.format(section_name)
            )

    def _save_to_h5group(self, h5group):
        h5group.create_dataset(
            'section_names',
            data=np.asarray(self._sections.index.to_numpy(), dtype=np.bytes_),
            dtype=h5py.special_dtype(vlen=str)
        )
        h5group.create_dataset(
            'parent_names',
            data=np.asarray(self._sections.to_numpy(), dtype=np.bytes_),
            dtype=h5py.special_dtype(vlen=str)
        )

    @staticmethod
    def _load_from_h5group(h5group):
        names = h5group['section_names'][:]
        parent_names = h5group['parent_names'][:]
        return HocMorphology(names, parent_names)


