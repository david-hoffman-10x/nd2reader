import unittest
from os import path
import six

from nd2reader.artificial import ArtificialND2
from nd2reader.common import get_version, parse_version, parse_date, _add_to_metadata


class TestCommon(unittest.TestCase):
    def setUp(self):
        dir_path = path.dirname(path.realpath(__file__))
        self.test_file = path.join(dir_path, 'test_data/test.nd2')

    def create_test_nd2(self):
        with ArtificialND2(self.test_file) as artificial:
            artificial.close()

    def test_parse_version_2(self):
        data = 'ND2 FILE SIGNATURE CHUNK NAME01!Ver2.2'
        actual = parse_version(data)
        expected = (2, 2)
        self.assertTupleEqual(actual, expected)

    def test_parse_version_3(self):
        data = 'ND2 FILE SIGNATURE CHUNK NAME01!Ver3.0'
        actual = parse_version(data)
        expected = (3, 0)
        self.assertTupleEqual(actual, expected)

    def test_get_version_from_file(self):
        self.create_test_nd2()

        with open(self.test_file, 'rb') as fh:
            version_tuple = get_version(fh)
            self.assertTupleEqual(version_tuple, (3, 0))

    def test_parse_date_24(self):
        date_format = "%m/%d/%Y  %H:%M:%S"
        date = '02/13/2016  23:43:37'
        textinfo = {six.b('TextInfoItem9'): six.b(date)}
        result = parse_date(textinfo)
        self.assertEqual(result.strftime(date_format), date)

    def test_parse_date_12(self):
        date_format = "%m/%d/%Y  %I:%M:%S %p"
        date = '02/13/2016  11:43:37 PM'
        textinfo = {six.b('TextInfoItem9'): six.b(date)}
        result = parse_date(textinfo)
        self.assertEqual(result.strftime(date_format), date)

    def test_add_to_meta_simple(self):
        metadata = {}
        _add_to_metadata(metadata, 'test', 'value')
        self.assertDictEqual(metadata, {'test': 'value'})

    def test_add_to_meta_new_list(self):
        metadata = {'test': 'value1'}
        _add_to_metadata(metadata, 'test', 'value2')
        self.assertDictEqual(metadata, {'test': ['value1', 'value2']})

    def test_add_to_meta_existing_list(self):
        metadata = {'test': ['value1', 'value2']}
        _add_to_metadata(metadata, 'test', 'value3')
        self.assertDictEqual(metadata, {'test': ['value1', 'value2', 'value3']})