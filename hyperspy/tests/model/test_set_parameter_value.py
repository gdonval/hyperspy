# Copyright 2007-2012 The HyperSpy developers
#
# This file is part of HyperSpy.
#
# HyperSpy is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# HyperSpy is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with HyperSpy. If not, see <http://www.gnu.org/licenses/>.


import numpy as np

from nose.tools import assert_true
from hyperspy._signals.spectrum import Spectrum
from hyperspy.hspy import create_model
from hyperspy.components import Gaussian


class TestSetParameterInModel:

    def setUp(self):
        g1 = Gaussian()
        g2 = Gaussian()
        g3 = Gaussian()
        s = Spectrum(np.arange(1000).reshape(10, 10, 10))
        m = create_model(s)
        m.append(g1)
        m.append(g2)
        m.append(g3)
        self.g1 = g1
        self.g2 = g2
        self.g3 = g3
        self.model = m

    def test_set_parameter_value1(self):
        m = self.model
        g1 = self.g1
        g2 = self.g2
        g3 = self.g3
        m.set_parameters_value('A', 20)
        assert_true(np.all(g1.A.map['values'] == 20))
        assert_true(np.all(g2.A.map['values'] == 20))
        assert_true(np.all(g3.A.map['values'] == 20))

    def test_set_parameter_value2(self):
        m = self.model
        g1 = self.g1
        g2 = self.g2
        g3 = self.g3
        m.set_parameters_value('A', 20, component_list=[g1, g2])
        assert_true(np.all(g1.A.map['values'] == 20))
        assert_true(np.all(g2.A.map['values'] == 20))
        assert_true(np.all(g3.A.map['values'] == 0))

    def test_set_parameter_value3(self):
        m = self.model
        g1 = self.g1
        g2 = self.g2
        g3 = self.g3
        m.set_parameters_value('A', 20, component_list=[g1], only_current=True)
        g1.A.map['values'][0][0] = g1.A.map['values'][0][0] - 20
        assert_true(np.all(g1.A.map['values'] == 0))
        assert_true(np.all(g2.A.map['values'] == 0))
        assert_true(np.all(g3.A.map['values'] == 0))

    def test_set_active_value1(self):
        m = self.model
        g1 = self.g1
        g2 = self.g2
        g3 = self.g3
        g1.active_is_multidimensional = True
        g2.active_is_multidimensional = True
        g3.active_is_multidimensional = True
        m.set_component_active_value(False)
        assert_true(np.all(np.logical_not(g1._active_array)))
        assert_true(np.all(np.logical_not(g2._active_array)))
        assert_true(np.all(np.logical_not(g3._active_array)))

    def test_set_active_value2(self):
        m = self.model
        g1 = self.g1
        g2 = self.g2
        g3 = self.g3
        g1.active_is_multidimensional = True
        g2.active_is_multidimensional = True
        g3.active_is_multidimensional = True
        m.set_component_active_value(False, component_list=[g1, g2])
        assert_true(np.all(np.logical_not(g1._active_array)))
        assert_true(np.all(np.logical_not(g2._active_array)))
        assert_true(np.all(g3._active_array))

    def test_set_active_value3(self):
        m = self.model
        g1 = self.g1
        g2 = self.g2
        g3 = self.g3
        g1.active_is_multidimensional = True
        g2.active_is_multidimensional = True
        g3.active_is_multidimensional = True
        m.set_component_active_value(False,
                                     component_list=[g1],
                                     only_current=True)
        g1._active_array[0][0] = not g1._active_array[0][0]
        assert_true(np.all(g1._active_array))
        assert_true(np.all(g2._active_array))
        assert_true(np.all(g3._active_array))
