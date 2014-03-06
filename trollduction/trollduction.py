# -*- coding: utf-8 -*-

# Copyright (c) 2014

# Author(s):

#   Panu Lahtinen <panu.lahtinen@fmi.fi>

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

'''Trollduction module
'''

from listener import ListenerContainer
#from publisher import Publisher
#from logger import Logger
from mpop.satellites import GenericFactory as GF
import datetime as dt
import time
from mpop.projector import get_area_def
import sys
import xml_read
from pprint import pprint
from pyorbital import astronomy
import numpy as np
import os

class Trollduction(object):
    '''Trollduction class for easy generation chain setup
    '''

    def __init__(self, td_config_file=None):
        '''Init Trollduction instance
        '''

        self.td_config = None
        self.product_config = None
        self.listener = None

        self.global_data = None
        self.local_data = None

        # Not yet in use
        self.publisher = None
        self.logger = None
        

        # read everything from the Trollduction config file
        if td_config_file is not None:
            self.update_td_config(td_config_file)


    def update_td_config(self, fname=None):
        '''Update Trollduction configuration from the given file.
        '''

        if fname is not None:
            self.td_config = read_config_file(fname)
        else:
            return
            
        print 'Trollduction configuration:'
        pprint(self.td_config)

        # Initialize/restart listener
        try:
            if self.listener is None:
                self.listener = ListenerContainer(\
                    data_type_list=self.td_config['listener_tag'])
            else:
                self.listener.restart_listener(self.td_config['listener_tag'])
        except KeyError:
            # TODO: logging
            print "Key 'listener_tag' is missing from", fname

        try:
            self.update_product_config(\
                fname=self.td_config['product_config_file'])
        except KeyError:
            print "Key 'product_config_file' is missing from", fname


    def update_product_config(self, fname=None):
        '''Update area definitions, associated product names, output
        filename prototypes and other relevant information from the
        given file.
        '''

        if fname is not None:
            product_config = read_config_file(fname)
        else:
            product_config = None

        # add checks, or do we just assume the config to be valid at
        # this point?
        self.product_config = product_config        
        if self.td_config['product_config_file'] != fname:
            self.td_config['product_config_file'] = fname

        print 'New product config:'
        pprint(self.product_config)


    def cleanup(self):
        '''Cleanup Trollduction before shutdown.
        '''
        # TODO: more cleanup, close threads, and stuff
        self.listener.stop()


    def shutdown(self):
        '''Shutdown trollduction.
        '''
        self.cleanup()
        sys.exit()


    def run_single(self):
        '''Run image production without threading.
        '''
        # TODO: Get relevant preprocessing function for this
        #   production chain type: single/multi, or
        #   swath, geo, granule, global_polar, global_geo, global_mixed
        # That is, gatherer for the multi-image/multi-granule types
        # preproc_func = getattr(preprocessing, self.production_type)

        while True:
            # wait for new messages
            msg = self.listener.parent_conn.recv()
            print msg
            # shutdown trollduction
            if '/StopTrollduction' in msg.subject:
                self.cleanup()
                # TODO: logging
                # TODO: message
                break # this should shutdown Trollduction
            # update trollduction config
            elif '/NewTrollductionConfig' in msg.subject:
                self.update_td_config(msg.data)
            # update product lists
            elif '/NewProductConfig' in msg.subject:
                self.update_product_config(msg.data)
            # process new file
            elif '/NewFileArrived' in msg.subject:
                time_slot = dt.datetime(int(msg.data['year']),
                                        int(msg.data['month']), 
                                        int(msg.data['day']),
                                        int(msg.data['hour']),
                                        int(msg.data['minute']))

                # orbit is empty string for meteosat, change it to None
                if msg.data['orbit'] == '':
                    msg.data['orbit'] = None

                t1a = time.time()

                # Create satellite scene
                self.global_data = GF.create_scene(\
                    satname=str(msg.data['satellite']),
                    satnumber=str(msg.data['satnumber']), 
                    instrument=str(msg.data['instrument']), 
                    time_slot=time_slot, 
                    orbit=str(msg.data['orbit']))

                # Update missing information to global_data.info{}
                self.global_data.info['satname'] = msg.data['satellite']
                self.global_data.info['satnumber'] = msg.data['satnumber']
                self.global_data.info['instrument'] = msg.data['instrument']
                self.global_data.info['orbit'] = msg.data['orbit']

                # Find maximum extent that is needed for all the
                # products to be made.
                # This really requires that area definitions can be
                # used directly
#                maximum_area_extent = get_maximum_extent(self.area_def_names)
#                maximum_area_extent = get_maximum_extent(['EuropeCanary'])

                # Load full data
                maximum_area_extent = None

                # Save unprojected data to netcdf4
                if self.product_config['common'].has_key('netcdf_file'):
                    unload = False
                    if maximum_area_extent is not None:
                        unload = True
                    self.write_netcdf(data_name='global_data', unload=unload)

                # Make images for each area
                for area in self.product_config['area']:

                    # Check if satellite is one that should be processed
                    if not self.check_satellite(area):
                        # if return value is False, skip this loop step
                        continue

                    t1b = time.time()

                    # Check which channels are needed. Unload
                    # unnecessary channels and load those that are not
                    # already available.
                    self.load_unload_channels(area['product'], 
                                              extent=maximum_area_extent)

                    # reproject to local domain
                    self.local_data = \
                        self.global_data.project(area['definition'], 
                                                 mode='nearest')
                    
                    # Save projected data to netcdf4
                    if area.has_key('netcdf_file'):
                        self.write_netcdf('local_data')

                    print "Data reprojected for area:", area['name']

                    # Draw requested images for this area.
                    self.draw_images(area)
                    print "Single area time elapsed time:", time.time()-t1b, 's'

                # Release memory
                self.local_data = None
                self.global_data = None

                print "Full time elapsed time:", time.time()-t1a, 's'
            else:
                # Unhandled message types end up here
                # No need to log these?
                pass


    def load_unload_channels(self, products, extent=None):
        '''Load channels for *extent* that are required for the given
        list of *products*. Unload channels that are unnecessary.
        '''

        # Rewritten using global_data.channels[].is_loaded()

        loaded_channels = []
        required_channels = []
        wavelengths = []

        # Get information which channels are loaded
        for chan in self.global_data.channels:
            required_channels.append(False)
            wavelengths.append(chan.wavelength_range)
            if chan.is_loaded():
                loaded_channels.append(True)
            else:
                loaded_channels.append(False)

        # Get a list of required channels
        for product in products:
            reqs = eval('self.global_data.image.'+ \
                            product['composite']+'.prerequisites')
            for req in reqs:
                for i in range(len(self.global_data.channels)):
                    if req >= np.min(wavelengths[i]) and \
                            req <= np.max(wavelengths[i]):
                        required_channels[i] = True
                        break

        to_load = []
        to_unload = []
        for i in range(len(self.global_data.channels)):
            if required_channels[i] and not loaded_channels[i]:
                to_load.append(self.global_data.channels[i].name)
            if not required_channels[i] and loaded_channels[i]:
                to_unload.append(self.global_data.channels[i].name)

        print "Loaded_channels:", loaded_channels
        print "Required_channels:", required_channels
        print "Channels to unload:", to_unload
        print "Channels to load:", to_load

        self.global_data.unload(*to_unload)
        self.global_data.load(to_load, extent)


    def check_satellite(self, config):
        '''Check if the current configuration allows the use of this
        satellite
        '''

        # Check the list of valid satellites
        if config.has_key('valid_satellite'):
            if self.global_data.info['satname'] +\
                    self.global_data.info['satnumber'] not in\
                    config['valid_satellite']:
                print self.global_data.info['satname'] + \
                    self.global_data.info['satnumber'], \
                    "not in list of valid satellites, skipping " +\
                    config['name']
                
                return False

        # Check the list of invalid satellites
        if config.has_key('invalid_satellite'):
            if self.global_data.info['satname'] +\
                    self.global_data.info['satnumber'] in\
                    config['invalid_satellite']:
                print self.global_data.info['satname'] + \
                    self.global_data.info['satnumber'], \
                    "is in the list of invalid satellites, " + \
                    "skipping " + config['name']
                return False

        return True


    def draw_images(self, area):
        '''Generate images from local data using given area name and
        product definitions.
        '''

        # Create images for each color composite
        for product in area['product']:

            # Check if satellite is one that should be processed
            if not self.check_satellite(product):
                # Skip this product, if the return value is True
                continue
            
            # Check if Sun zenith angle limits match this product
            if product.has_key('sunzen_night_minimum') or \
                    product.has_key('sunzen_day_maximum'):
                if not self.check_sunzen(product, area_def=\
                                             get_area_def(area['definition'])):
                    # If the return value is False, skip this product
                    continue

            # Parse image filename
            fname = self.parse_filename(area, product)

            try:
                # Check if this combination is defined
                func = getattr(self.local_data.image, product['composite'])
                img = func()            
                img.save(fname)
                print "Image", fname, "saved."

                # TODO: log succesful production
                # TODO: publish message
            except AttributeError:
                # TODO: log incorrect product name
                print "Incorrect product name:", product['name'], \
                    "for area", area['name']
            except KeyError:
                # TODO: log missing channel
                print "Missing channel on", product['name'], \
                    "for area", area['name']
            except:
                err = sys.exc_info()[0]
                # TODO: log other errors
                print "Error", err, "on", product['name'], \
                    "for area", area['name']

        # TODO: log completion of this area def
        # TODO: publish completion of this area def



    def write_netcdf(self, data_name='global_data', unload=False):
        '''Write the data as netCDF4.
        '''

        try:
            data = getattr(self, data_name)
        except AttributeError:
            print "No such data", data_name
            return

        # parse filename
        fname = self.parse_filename(fname_key='netcdf_file')

        print "Saving %s." % fname
        # Load all the data
        data.load()
        # Save the data
        data.save(fname, to_format='netcdf4')

        if unload:
            loaded_channels = [ch.name for ch in data.channels]
            data.unload(*loaded_channels)


    def parse_filename(self, area=None, product=None, fname_key='filename'):
        '''Parse filename.  Parameter *area* is for area-level
        configuration dictionary, *product* for product-level
        configuration dictionary.  Parameter *fname_key* tells which
        dictionary key holds the filename pattern.
        '''
        try:
            out_dir = product['output_dir']
        except (KeyError, TypeError):
            try:
                out_dir = area['output_dir']
            except (KeyError, TypeError):
                out_dir = self.product_config['common']['output_dir']
            
        try:
            fname = product[fname_key]
        except (KeyError, TypeError):
            try:
                fname = area[fname_key]
            except (KeyError, TypeError):
                fname = self.product_config['common'][fname_key]

        fname = os.path.join(out_dir, fname)

        try:
            time_slot = self.local_data.time_slot
        except AttributeError:
            time_slot = self.global_data.time_slot
        fname = fname.replace('%Y', '%04d' % time_slot.year)
        fname = fname.replace('%m', '%02d' % time_slot.month)
        fname = fname.replace('%d', '%02d' % time_slot.day)
        fname = fname.replace('%H', '%02d' % time_slot.hour)
        fname = fname.replace('%M', '%02d' % time_slot.minute)
        if area is not None:
            fname = fname.replace('%(areaname)', area['name'])
        if product is not None:
            fname = fname.replace('%(composite)', product['name'])
        fname = fname.replace('%(satellite)', 
                              self.global_data.info['satname'] + \
                                  self.global_data.info['satnumber'])
        fname = fname.replace('%(orbit)', self.global_data.info['orbit'])
        fname = fname.replace('%(instrument)', 
                              self.global_data.info['instrument'])
        fname = fname.replace('%(ending)', 'png')
        
        return fname


    def check_sunzen(self, config, area_def=None, data_name='local_data', 
                     lonlat=None):
        '''Check if the data is within Sun zenith angle limits.
        '''

        try:
            data = getattr(self, data_name)
        except AttributeError:
            print "No such data", data_name
            return False

        if area_def is None and lonlat is None:
            print 'No area definition or coordinates given.'
            return False

        # This can be later expanded to use the given (lon, lat)
        # location
        y_idx = int(area_def.y_size/2)
        x_idx = int(area_def.x_size/2)

        # Check availability of coordinates, load if necessary
        if data.area.lons is None:
            print "Load coordinates for", data_name
            data.area.lons, data.area.lats = data.area.get_lonlats()

        # Check availability of Sun zenith angles, calculate if necessary
        try:
            data.__getattribute__('sun_zen')
        except AttributeError:
            print "Calculate Sun zenith angles for", data_name
            data.sun_zen = astronomy.sun_zenith_angle(data.time_slot,
                                                      data.area.lons,
                                                      data.area.lats)

        # Check if Sun is too low (day-only products)
        try:
            if float(config['sunzen_day_maximum']) < \
                    data.sun_zen[y_idx, x_idx]:
                print 'Sun too low for day-time product.'
                return False
        except KeyError:
            pass

        # Check if Sun is too high (night-only products)
        try:
            if float(config['sunzen_night_minimum']) > \
                    data.sun_zen[y_idx, x_idx]:
                print 'Sun too high for night-time product.'
                return False
        except KeyError:
            pass

        return True

          
def read_config_file(fname=None):
    '''Read config file to dictionary.
    '''
    
    # TODO: check validity
    # TODO: logging
    
    if fname is None:
        return None
    else:
        return xml_read.parse_xml(xml_read.get_root(fname))


def get_maximum_extent(area_def_names):
    '''Get maximum extend needed to produce all defined areas.
    '''
    maximum_area_extent = [None, None, None, None]
    for area in area_def_names:
        extent = get_area_def(area)
        
        if maximum_area_extent[0] is None:
            maximum_area_extent = list(extent.area_extent)
        else:
            if maximum_area_extent[0] > extent.area_extent[0]:
                maximum_area_extent[0] = extent.area_extent[0]
            if maximum_area_extent[1] > extent.area_extent[1]:
                maximum_area_extent[1] = extent.area_extent[1]
            if maximum_area_extent[2] < extent.area_extent[2]:
                maximum_area_extent[2] = extent.area_extent[2]
            if maximum_area_extent[3] < extent.area_extent[3]:
                maximum_area_extent[3] = extent.area_extent[3]

    return maximum_area_extent

