# General_imports
import numpy as np
import matplotlib.pyplot as plt
import sys
import warnings

# BLonD_Common imports
from ...rf_functions import potential as pot
from ...maths import calculus as calc
from ...datatypes import beam_data as bDat
from ...beam_dynamics import bucket as buck
from ...devtools import exceptions as excpt


class Beam_Parameters:
    
    def __init__(self, ring, rf, use_samples = None, init_coord = None, 
                 harmonic_divide = 1, potential_resolution = 1000,
                 bunch_emittance = 0, calc_params = True):
        
        self.ring = ring
        self.rf = rf
        
        if use_samples is None:
            use_samples = list(range(len(self.ring.use_turns)))
        
        self.use_samples = use_samples
        self.n_samples = len(self.use_samples)

        if init_coord is None:
            init_coord = 0.5*ring.t_rev[0]/harmonic_divide
        if not hasattr(init_coord, '__iter__'):
            init_coord = (init_coord,)

        self.init_coord = init_coord

        if harmonic_divide % 1 == 0:
            self.harmonic_divide = int(harmonic_divide)
        else:
            raise excpt.InputError("harmonic_divide should be int-like")

        if harmonic_divide % 1 == 0:
            self.potential_resolution = int(potential_resolution)
        else:
            raise excpt.InputError("potential_resolution should be int-like")
        
        self.volt_wave_array = np.zeros([self.n_samples, 
                                         self.potential_resolution])
        self.time_window_array = np.zeros([self.n_samples, 
                                           self.potential_resolution])
        self.potential_well_array = np.zeros([self.n_samples, 
                                              self.potential_resolution])

        if not isinstance(bunch_emittance, bDat.emittance):
            self.bunch_emittance = bDat.emittance(bunch_emittance, units = 'eVs').reshape(\
                                               n_sections = len(self.init_coord), 
                                               use_time = self.ring.cycle_time, 
                                               use_turns = self.ring.use_turns)
        else:
            self.bunch_emittance = bunch_emittance.reshape(len(self.init_coord),
                                                           use_time = self.ring.cycle_time, 
                                               use_turns = self.ring.use_turns)
        
        if calc_params:
            self.calc_potential_wells()
            self.track_synchronous()
            self.buckets = {}
            self.calc_buckets()
            self.bucket_parameters(True)
    
    
    def full_update(self):

        self.calc_potential_wells()
        self.track_synchronous()
        self.buckets = {}
        self.calc_buckets()
        self.bucket_parameters(True)
        
    
    def calc_potential_wells(self, sample = None):

        '''
        Calculate potential well at all or specified sample

        Parameters
        ----------
        sample : None, int
            if not None:
               well calculated only for specified sample
        '''

        if sample is None:
            # for s in range(self.n_samples):
            for i, s in enumerate(self.use_samples):
                time, well, vWave = self.sample_potential_well(s)
                self.volt_wave_array[i] = vWave
                self.time_window_array[i] = time
                self.potential_well_array[i] = well

        else:
            time, well, vWave = self.sample_potential_well(sample)
            self.volt_wave_array[sample] = vWave
            self.time_window_array[sample] = time
            self.potential_well_array[sample] = well
    
    
    
    def track_synchronous(self, start_sample = 0):
        
        #If no start points specified create a single particle at the lowest 
        #and leftest minimum
        if self.init_coord is None:
            point = np.where(self.potential_well_array[0]
                             == np.min(self.potential_well_array[0]))[0][0]
            self.init_coord = [self.time_window_array[0][point]]

        if start_sample == 0:
            self.particle_tracks = np.zeros([len(self.init_coord),
                                            self.n_samples])
            if len(self.init_coord) == 1:
                bunching = 'single_bunch'
            else:
                bunching = 'multi_bunch'
            self.particle_tracks \
                    = bDat.synchronous_phase.zeros([len(self.init_coord),
                                                  self.n_samples],
                                                {'timebase': 'by_turn',
                                                 'bunching': bunching,
                                                 'units': 's'})

        self.n_particles = len(self.init_coord)

        #Loop over all particles positioning them in closest minimum to 
        #declared start point
        for p in range(self.n_particles):

            startPoint = np.where(self.time_window_array[0]
                                  <= self.init_coord[p])[0][-1]
            self.particle_tracks[p][0] = self.time_window_array[0][startPoint]

            locs, values \
                    = calc.minmax_location_cubic(self.time_window_array[0],
                                                 self.potential_well_array[0],
                                                 mest = int(3*np.max(self.rf.harmonic)))
            locs = locs[0]
            offsets = np.abs(self.particle_tracks[p][0] - locs)
            newLoc = np.where(offsets == np.min(offsets))[0][0]
                
            self.particle_tracks[p][0] = locs[newLoc]
        
        #Loop over all particles and all but first sample, at each sample new 
        #particle location is nearest minimum in potential well
        for p in range(self.n_particles):
            for t in range(start_sample+1, self.n_samples):
                locs, values \
                        = calc.minmax_location_cubic(self.time_window_array[t],
                                                self.potential_well_array[t],
                                        mest = int(3*np.max(self.rf.harmonic)))
                locs = locs[0]
                offsets = np.abs(self.particle_tracks[p][t-1] - locs)
                newLoc = np.where(offsets == np.min(offsets))[0][0]
                self.particle_tracks[p][t] = locs[newLoc]


    
    def calc_buckets(self):

        '''
        Create and store all buckets
        '''


        for s in range(self.n_samples):
            bucket_list = self.create_sample_buckets(s)
            for p in range(self.n_particles):
                self.buckets[(s, p)] = bucket_list[p]
    

    def sample_potential_well(self, sample, volts = None):

        '''
        Calculate potential well at given sample with existing or passed voltage

        Parameters
        ----------
        sample : int
            sample number to use
        volts : None, np.array or list
            if None:
                Use voltage from self.rfprogram to define potential well
            else:
                Use passed voltage to define potential well
        Returns
        -------
        time : array
            time axis of potential well
        well : array
            potential well amplitude
        vWave : array
            full voltage used to calculate potential well
            returned if volts is None
        '''        
        
        ringPars, rfPars = self._get_pars(sample)
        timeBounds = self._time_bounds(ringPars['t_rev'])
        vTime, vWave = self._calc_volts(ringPars, rfPars, timeBounds, volts)
        
        time, well = self.calc_well(vTime, vWave, ringPars)
        
        if volts is None:
            return time, well, vWave
        else:
            return time, well



    def _calc_volts(self, ringPars, rfPars, timeBounds, volts):

        if volts is None:
             vTime, vWave = pot.rf_voltage_generation(self.potential_resolution,
                                                    ringPars['t_rev'],
                                                    rfPars['voltage'],
                                                    rfPars['harmonic'],
                                                    rfPars['phi_rf_d'],
                                                    time_bounds = timeBounds)
        else:
            vWave = volts
            vTime = np.linspace(timeBounds[0], timeBounds[1], 
                                self.potential_resolution)
        
        return vTime, vWave


    
    
    def _time_bounds(self, t_rev):
        
        tRight = t_rev/self.harmonic_divide
        tLeft = -0.1*tRight
        tRight *= 1.1
        
        return (tLeft, tRight)
    
    
    def _get_pars(self, sample):
        
        ringPars = self.ring.parameters_at_sample(sample)
        rfPars = self.rf.parameters_at_sample(sample)
        
        return ringPars, rfPars
    
    
    def calc_well(self, time, volts, ringPars):
    
        time, well, _ = pot.rf_potential_generation_cubic(time, volts, 
                                                          ringPars['eta_0'], 
                                                          ringPars['charge'],
                                                          ringPars['t_rev'], 
                                                          ringPars['delta_E'])
        return time, well
    
    
    def cut_well(self, sample, particle):
        
        '''
        Calculate potential well and inner wells at a specified sample and 
        particle
        
        Parameters
        ----------
        sample : int
            sample number to use
        particle : int
            particle number to use

        Returns
        -------
        time : np.array
            Time component of calculated well
        well : np.array
            Potential well
        '''

        inTime = self.time_window_array[sample]
        inWell = self.potential_well_array[sample]
        inWell -= np.min(inWell)

        #TODO: revisit relative_max_val_precision
        try:
            maxLocs, _, _, _, _ = pot.find_potential_wells_cubic(inTime, inWell,
                                     mest = int(1E5),
                                     relative_max_val_precision_limit=1E-4)
        except:
            plt.plot(inTime, inWell)
            plt.show()
            raise
        
        times, wells = pot.potential_well_cut_cubic(inTime, inWell, maxLocs)
        particleLoc = self.particle_tracks[particle][sample]
        
        times, wells = pot.choose_potential_wells(particleLoc, times, wells)
        
        return times, wells
    

    def create_particle_bucket(self, sample, particle):

        '''
        Create new bucket at a specified sample for a specified particle.  If time and well are None
        the stored parameters will be used.  If time and well are specified particle becomes the required
        synchronous phase point rather than a counter to the particle_tracks array.

        Parameters
        ----------
        sample : int
            sample number to use
        particle : int
            particle number to use
        time : None, list
            if list:
                used to find bucket
        well : None, list
            if list:
                
        '''

        pars = self.ring.parameters_at_sample(sample)

        time, well = self.cut_well(sample, particle)

        return buck.Bucket(time, well, pars['beta'], pars['energy'],
                           pars['eta_0'])
    
    
    def bucket_parameters(self, update_bunch_parameters = False,
                          over_fill = False):

        '''
        Store bucket heights, areas, lengths and centers through the ramp
        '''


        n_pars = self.particle_tracks.shape[0]

        if len(self.init_coord) == 1:
            bunching = 'single_bunch'
        else:
            bunching = 'multi_bunch'

        data_type = {'timebase': 'by_turn', 'bunching': bunching}

        self.heights = bDat.height.zeros([n_pars, self.n_samples], 
                                       {**data_type, 'units': 'eV', 
                                        'height_type': 'half_height'})
        self.bunch_heights = bDat.height.zeros([n_pars, self.n_samples], 
                                             {**data_type, 'units': 'eV', 
                                              'height_type': 'half_height'})
        self.areas = bDat.acceptance.zeros([n_pars, self.n_samples], 
                                         {**data_type, 'units': 'eV'})
        self.bunch_emittances = bDat.emittance.zeros([n_pars, self.n_samples], 
                                               {**data_type, 
                                            'emittance_type': 'matched_area',
                                            'units': 'eVs'})
        self.lengths = bDat.length.zeros([n_pars, self.n_samples], 
                                               {**data_type, 
                                                'length_type': 'full',
                                                'units': 's'})
        self.bunch_lengths = bDat.length.zeros([n_pars, self.n_samples], 
                                               {**data_type, 
                                                'length_type': 'full',
                                                'units': 's'})

        for n in range(n_pars):
            buckets = self.buckets_by_particle(n)
            for b in range(len(buckets)):
                if update_bunch_parameters:
                    try:
                        buckets[b].bunch_emittance = self.bunch_emittance[n, b]
                    except excpt.BunchSizeError:
                        if over_fill:
                            warnings.warn(f"Requested emittance "
                                          +f"{self.bunch_emittance[n, b]} "
                                          +f"exceeds acceptance of bucket "
                                          +f"{n, b}, using bucket acceptance"
                                          +f"of {buckets[b].area} instead.")
                            buckets[b].bunch_emittance = buckets[b].area
                        else:
                            raise
                    
                self.bunch_heights[n, b] = buckets[b].bunch_height
                self.heights[n, b] = buckets[b].half_height
                self.areas[n, b] = buckets[b].area
                self.bunch_emittances[n, b] = buckets[b].bunch_emittance
                self.lengths[n, b] = buckets[b].length
                self.bunch_lengths[n, b] = buckets[b].bunch_length

        
        
        
    def create_sample_buckets(self, sample):

        '''
        Create new bucket at a specified sample for all particles.

        Parameters
        ----------
        sample : int
            sample number to use
        '''

        bucket_list = []
        for p in range(self.n_particles):
            bucket_list.append(self.create_particle_bucket(sample, p))

        return bucket_list
    
    
    
    def buckets_by_particle(self, particle):

        '''
        Return list of buckets for a specified particle

        Parameters
        ----------
        particle : int
            particle to return buckets for

        Returns
        -------
        bucket_list : list
            list of buckets through program for specified particle
        '''

        return [self.buckets[key] for key in self.buckets.keys() if 
                                                        key[1] == particle] 
