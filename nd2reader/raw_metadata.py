import re

from nd2reader.common import read_chunk, read_array, read_metadata, parse_date
import xmltodict
import six
import numpy as np


def ignore_missing(func):
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except:
            return None

    return wrapper


class RawMetadata(object):
    def __init__(self, fh, label_map):
        self._fh = fh
        self._label_map = label_map
        self._metadata_parsed = None

    @property
    def __dict__(self):
        """
        Returns the parsed metadata in dictionary form
        :return: 
        """
        return self.get_parsed_metadata()

    def get_parsed_metadata(self):
        """
        Returns the parsed metadata in dictionary form
        :return: 
        """

        if self._metadata_parsed is not None:
            return self._metadata_parsed

        self._metadata_parsed = {
            "height": self.image_attributes[six.b('SLxImageAttributes')][six.b('uiHeight')],
            "width": self.image_attributes[six.b('SLxImageAttributes')][six.b('uiWidth')],
            "date": parse_date(self.image_text_info[six.b('SLxImageTextInfo')]),
            "fields_of_view": self._parse_fields_of_view(),
            "frames": self._parse_frames(),
            "z_levels": self._parse_z_levels(),
            "total_images_per_channel": self._parse_total_images_per_channel(),
            "channels": self._parse_channels(),
            "pixel_microns": self.image_calibration.get(six.b('SLxCalibration'), {}).get(six.b('dCalibration')),
        }

        self._metadata_parsed['num_frames'] = len(self._metadata_parsed['frames'])

        self._parse_roi_metadata()
        self._parse_experiment_metadata()

        return self._metadata_parsed

    def _parse_channels(self):
        """
        These are labels created by the NIS Elements user. Typically they may a short description of the filter cube
        used (e.g. "bright field", "GFP", etc.)

        :rtype: list

        """
        channels = []
        metadata = self.image_metadata_sequence[six.b('SLxPictureMetadata')][six.b('sPicturePlanes')]
        try:
            validity = self.image_metadata[six.b('SLxExperiment')][six.b('ppNextLevelEx')][six.b('')][0][
                six.b('ppNextLevelEx')][six.b('')][0][six.b('pItemValid')]
        except (KeyError, TypeError):
            # If none of the channels have been deleted, there is no validity list, so we just make one
            validity = [True for _ in metadata]
        # Channel information is contained in dictionaries with the keys a0, a1...an where the number
        # indicates the order in which the channel is stored. So by sorting the dicts alphabetically
        # we get the correct order.
        for (label, chan), valid in zip(sorted(metadata[six.b('sPlaneNew')].items()), validity):
            if not valid:
                continue
            channels.append(chan[six.b('sDescription')].decode("utf8"))
        return channels

    def _parse_fields_of_view(self):
        """
        The metadata contains information about fields of view, but it contains it even if some fields
        of view were cropped. We can't find anything that states which fields of view are actually
        in the image data, so we have to calculate it. There probably is something somewhere, since
        NIS Elements can figure it out, but we haven't found it yet.

        :rtype:    list

        """
        return self._parse_dimension(r""".*?XY\((\d+)\).*?""")

    def _parse_frames(self):
        """
        The number of cycles.

        :rtype:     list

        """
        return self._parse_dimension(r""".*?T'?\((\d+)\).*?""")

    def _parse_z_levels(self):
        """
        The different levels in the Z-plane. Just a sequence from 0 to n.

        :rtype:    list

        """
        return self._parse_dimension(r""".*?Z\((\d+)\).*?""")

    def _parse_dimension_text(self):
        """
        While there are metadata values that represent a lot of what we want to capture, they seem to be unreliable.
        Sometimes certain elements don't exist, or change their data type randomly. However, the human-readable text
        is always there and in the same exact format, so we just parse that instead.

        :rtype:    str

        """
        dimension_text = six.b("")
        textinfo = self.image_text_info[six.b('SLxImageTextInfo')].values()

        for line in textinfo:
            if six.b("Dimensions:") in line:
                entries = line.split(six.b("\r\n"))
                for entry in entries:
                    if entry.startswith(six.b("Dimensions:")):
                        return entry

        return dimension_text

    def _parse_dimension(self, pattern):
        """
        :param pattern:    a valid regex pattern
        :type pattern:    str

        :rtype:    list of int

        """
        dimension_text = self._parse_dimension_text()
        if six.PY3:
            dimension_text = dimension_text.decode("utf8")
        match = re.match(pattern, dimension_text)
        if not match:
            return [0]
        count = int(match.group(1))
        return list(range(count))

    def _parse_total_images_per_channel(self):
        """
        The total number of images per channel. Warning: this may be inaccurate as it includes "gap" images.

        :rtype: int

        """
        return self.image_attributes[six.b('SLxImageAttributes')][six.b('uiSequenceCount')]

    def _parse_roi_metadata(self):
        """
        Parse the raw ROI metadata.
        :return:
        """
        if self.roi_metadata is None or not six.b('RoiMetadata_v1') in self.roi_metadata:
            return

        raw_roi_data = self.roi_metadata[six.b('RoiMetadata_v1')]

        number_of_rois = raw_roi_data[six.b('m_vectGlobal_Size')]

        roi_objects = []
        for i in range(number_of_rois):
            current_roi = raw_roi_data[six.b('m_vectGlobal_%d' % i)]
            roi_objects.append(self._parse_roi(current_roi))

        self._metadata_parsed['rois'] = roi_objects

    def _parse_roi(self, raw_roi_dict):
        """
        Extract the vector animation parameters from the ROI.
        This includes the position and size at the given timepoints.
        :param raw_roi_dict:
        :return:
        """
        number_of_timepoints = raw_roi_dict[six.b('m_vectAnimParams_Size')]

        roi_dict = {
            "timepoints": [],
            "positions": [],
            "sizes": [],
            "shape": self._parse_roi_shape(raw_roi_dict[six.b('m_sInfo')][six.b('m_uiShapeType')]),
            "type": self._parse_roi_type(raw_roi_dict[six.b('m_sInfo')][six.b('m_uiInterpType')])
        }
        for i in range(number_of_timepoints):
            roi_dict = self._parse_vect_anim(roi_dict, raw_roi_dict[six.b('m_vectAnimParams_%d' % i)])

        # convert to NumPy arrays
        roi_dict["timepoints"] = np.array(roi_dict["timepoints"], dtype=np.float)
        roi_dict["positions"] = np.array(roi_dict["positions"], dtype=np.float)
        roi_dict["sizes"] = np.array(roi_dict["sizes"], dtype=np.float)

        return roi_dict

    @staticmethod
    def _parse_roi_shape(shape):
        if shape == 3:
            return 'rectangle'
        elif shape == 9:
            return 'circle'

        return None

    @staticmethod
    def _parse_roi_type(type_no):
        if type_no == 4:
            return 'stimulation'
        elif type_no == 3:
            return 'reference'
        elif type_no == 2:
            return 'background'

        return None

    def _parse_vect_anim(self, roi_dict, animation_dict):
        """
        Parses a ROI vector animation object and adds it to the global list of timepoints and positions.
        :param animation_dict:
        :return:
        """
        roi_dict["timepoints"].append(animation_dict[six.b('m_dTimeMs')])

        image_width = self._metadata_parsed["width"] * self._metadata_parsed["pixel_microns"]
        image_height = self._metadata_parsed["height"] * self._metadata_parsed["pixel_microns"]

        # positions are taken from the center of the image as a fraction of the half width/height of the image
        position = np.array((0.5 * image_width * (1 + animation_dict[six.b('m_dCenterX')]),
                             0.5 * image_height * (1 + animation_dict[six.b('m_dCenterY')]),
                             animation_dict[six.b('m_dCenterZ')]))
        roi_dict["positions"].append(position)

        size_dict = animation_dict[six.b('m_sBoxShape')]

        # sizes are fractions of the half width/height of the image
        roi_dict["sizes"].append((size_dict[six.b('m_dSizeX')] * 0.25 * image_width,
                                  size_dict[six.b('m_dSizeY')] * 0.25 * image_height,
                                  size_dict[six.b('m_dSizeZ')]))
        return roi_dict

    def _parse_experiment_metadata(self):
        """
        Parse the metadata of the ND experiment
        :return:
        """
        if not six.b('SLxExperiment') in self.image_metadata:
            return

        raw_data = self.image_metadata[six.b('SLxExperiment')]

        experimental_data = {
            'description': 'unknown',
            'loops': []
        }

        if six.b('wsApplicationDesc') in raw_data:
            experimental_data['description'] = raw_data[six.b('wsApplicationDesc')].decode('utf8')

        if six.b('uLoopPars') in raw_data:
            experimental_data['loops'] = self._parse_loop_data(raw_data[six.b('uLoopPars')])

        self._metadata_parsed['experiment'] = experimental_data

    def _parse_loop_data(self, loop_data):
        """
        Parse the experimental loop data
        :param loop_data:
        :return:
        """
        if six.b('uiPeriodCount') not in loop_data or loop_data[six.b('uiPeriodCount')] == 0:
            return []

        if six.b('pPeriod') not in loop_data:
            return []

        # take the first dictionary element, it contains all loop data
        loops = loop_data[six.b('pPeriod')][list(loop_data[six.b('pPeriod')].keys())[0]]

        # take into account the absolute time in ms
        time_offset = 0

        parsed_loops = []

        for loop in loops:
            # duration of this loop
            duration = loop[six.b('dDuration')]

            # uiLoopType == 6 is a stimulation loop
            is_stimulation = loop[six.b('uiLoopType')] == 6

            # sampling interval in ms
            interval = loop[six.b('dAvgPeriodDiff')]

            parsed_loop = {
                'start': time_offset,
                'duration': duration,
                'stimulation': is_stimulation,
                'sampling_interval': interval
            }

            parsed_loops.append(parsed_loop)

            # increase the time offset
            time_offset += duration

        return parsed_loops

    @property
    @ignore_missing
    def image_text_info(self):
        return read_metadata(read_chunk(self._fh, self._label_map.image_text_info), 1)

    @property
    @ignore_missing
    def image_metadata_sequence(self):
        return read_metadata(read_chunk(self._fh, self._label_map.image_metadata_sequence), 1)

    @property
    @ignore_missing
    def image_calibration(self):
        return read_metadata(read_chunk(self._fh, self._label_map.image_calibration), 1)

    @property
    @ignore_missing
    def image_attributes(self):
        return read_metadata(read_chunk(self._fh, self._label_map.image_attributes), 1)

    @property
    @ignore_missing
    def x_data(self):
        return read_array(self._fh, 'double', self._label_map.x_data)

    @property
    @ignore_missing
    def y_data(self):
        return read_array(self._fh, 'double', self._label_map.y_data)

    @property
    @ignore_missing
    def z_data(self):
        return read_array(self._fh, 'double', self._label_map.z_data)

    @property
    @ignore_missing
    def roi_metadata(self):
        return read_metadata(read_chunk(self._fh, self._label_map.roi_metadata), 1)

    @property
    @ignore_missing
    def pfs_status(self):
        return read_array(self._fh, 'int', self._label_map.pfs_status)

    @property
    @ignore_missing
    def pfs_offset(self):
        return read_array(self._fh, 'int', self._label_map.pfs_offset)

    @property
    @ignore_missing
    def camera_exposure_time(self):
        return read_array(self._fh, 'double', self._label_map.camera_exposure_time)

    @property
    @ignore_missing
    def lut_data(self):
        return xmltodict.parse(read_chunk(self._fh, self._label_map.lut_data))

    @property
    @ignore_missing
    def grabber_settings(self):
        return xmltodict.parse(read_chunk(self._fh, self._label_map.grabber_settings))

    @property
    @ignore_missing
    def custom_data(self):
        return xmltodict.parse(read_chunk(self._fh, self._label_map.custom_data))

    @property
    @ignore_missing
    def app_info(self):
        return xmltodict.parse(read_chunk(self._fh, self._label_map.app_info))

    @property
    @ignore_missing
    def camera_temp(self):
        camera_temp = read_array(self._fh, 'double', self._label_map.camera_temp)
        if camera_temp:
            for temp in map(lambda x: round(x * 100.0, 2), camera_temp):
                yield temp

    @property
    @ignore_missing
    def acquisition_times(self):
        acquisition_times = read_array(self._fh, 'double', self._label_map.acquisition_times)
        if acquisition_times:
            for acquisition_time in map(lambda x: x / 1000.0, acquisition_times):
                yield acquisition_time

    @property
    @ignore_missing
    def image_metadata(self):
        if self._label_map.image_metadata:
            return read_metadata(read_chunk(self._fh, self._label_map.image_metadata), 1)
