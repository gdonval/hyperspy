# -*- coding: utf-8 -*-
# Copyright 2007-2011 The HyperSpy developers
#
# This file is part of  HyperSpy.
#
#  HyperSpy is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
#  HyperSpy is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with  HyperSpy.  If not, see <http://www.gnu.org/licenses/>.

import copy
import warnings

from hyperspy.model import Model
from hyperspy.components import EELSCLEdge
from hyperspy.components import PowerLaw
from hyperspy.misc.ipython_tools import get_interactive_ns
from hyperspy.defaults_parser import preferences
import hyperspy.messages as messages
from hyperspy import components
from hyperspy._signals.eels import EELSSpectrum


def _give_me_delta(master, slave):
    return lambda x: x + slave - master


def _give_me_idelta(master, slave):
    return lambda x: x - slave + master


class EELSModel(Model):

    """Build a fit a model

    Parameters
    ----------
    spectrum : an Spectrum (or any Spectrum subclass) instance
    auto_background : boolean
        If True, and if spectrum is an EELS instance adds automatically
        a powerlaw to the model and estimate the parameters by the
        two-area method.
    auto_add_edges : boolean
        If True, and if spectrum is an EELS instance, it will
        automatically add the ionization edges as defined in the
        Spectrum instance. Adding a new element to the spectrum using
        the components.EELSSpectrum.add_elements method automatically
        add the corresponding ionisation edges to the model.
    ll : {None, EELSSpectrum}
        If an EELSSPectrum is provided, it will be assumed that it is
        a low-loss EELS spectrum, and it will be used to simulate the
        effect of multiple scattering by convolving it with the EELS
        spectrum.
    GOS : {'hydrogenic', 'Hartree-Slater', None}
        The GOS to use when auto adding core-loss EELS edges.
        If None it will use the Hartree-Slater GOS if
        they are available, otherwise it will use the hydrogenic GOS.

    """

    def __init__(self, spectrum, auto_background=True,
                 auto_add_edges=True, ll=None,
                 GOS=None, *args, **kwargs):
        Model.__init__(self, spectrum, *args, **kwargs)
        self._suspend_auto_fine_structure_width = False
        self.convolved = False
        self.low_loss = ll
        self.GOS = GOS
        self.edges = list()
        if auto_background is True:
            interactive_ns = get_interactive_ns()
            background = PowerLaw()
            background.name = 'background'
            interactive_ns['background'] = background
            self.append(background)

        if self.spectrum.subshells and auto_add_edges is True:
            self._add_edges_from_subshells_names()

    @property
    def spectrum(self):
        return self._spectrum

    @spectrum.setter
    def spectrum(self, value):
        if isinstance(value, EELSSpectrum):
            self._spectrum = value
            self.spectrum._are_microscope_parameters_missing()
        else:
            raise ValueError(
                "This attribute can only contain an EELSSpectrum "
                "but an object of type %s was provided" %
                str(type(value)))

    def _touch(self):
        """Run model setup tasks

        This function must be called everytime that we add or remove components
        from the model.
        It creates the bookmarks self.edges and self._background_components and
        configures the edges by setting the energy_scale attribute and setting
        the fine structure.

        """
        self._Model__touch()
        self.edges = []
        self._background_components = []
        for component in self:
            if isinstance(component, EELSCLEdge):
                component.set_microscope_parameters(
                    E0=self.spectrum.metadata.Acquisition_instrument.TEM.beam_energy,
                    alpha=self.spectrum.metadata.Acquisition_instrument.TEM.convergence_angle,
                    beta=self.spectrum.metadata.Acquisition_instrument.TEM.Detector.EELS.collection_angle,
                    energy_scale=self.axis.scale)
                component.energy_scale = self.axis.scale
                component._set_fine_structure_coeff()
                if component.onset_energy.value < \
                        self.axis.axis[self.channel_switches][0]:
                    component.isbackground = True
                if component.isbackground is not True:
                    self.edges.append(component)
                else:
                    component.fine_structure_active = False
                    component.fine_structure_coeff.free = False
                    component.backgroundtype = "edge"
                    self._background_components.append(component)
            elif (isinstance(component, PowerLaw) or
                  component.isbackground is True):
                self._background_components.append(component)

        if self.edges:
            self.edges.sort(key=EELSCLEdge._onset_energy)
            self.resolve_fine_structure()
        if len(self._background_components) > 1:
            self._backgroundtype = "mix"
        elif len(self._background_components) == 1:
            self._backgroundtype = \
                self._background_components[0].__repr__()
            if self._firstimetouch and self.edges:
                self.two_area_background_estimation()
                self._firstimetouch = False

    @property
    def _active_edges(self):
        return [edge for edge in self.edges if edge.active]

    @property
    def _active_background_components(self):
        return [bc for bc in self._background_components if bc.active]

    def _add_edges_from_subshells_names(self, e_shells=None,
                                        copy2interactive_ns=True):
        """Create the Edge instances and configure them appropiately
        Parameters
        ----------
        e_shells : list of strings
        copy2interactive_ns : bool
            If True, variables with the format Element_Shell will be
            created in IPython's interactive shell
        """
        interactive_ns = get_interactive_ns()
        if e_shells is None:
            e_shells = list(self.spectrum.subshells)
        e_shells.sort()
        master_edge = EELSCLEdge(e_shells.pop(), self.GOS)
        # If self.GOS was None, the GOS is set by eels_cl_edge so
        # we reassing the value of self.GOS
        self.GOS = master_edge.GOS._name
        self.append(master_edge)
        interactive_ns[self[-1].name] = self[-1]
        element = master_edge.element
        interactive_ns[element] = []
        interactive_ns[element].append(self[-1])
        while len(e_shells) > 0:
            next_element = e_shells[-1].split('_')[0]
            if next_element != element:
                # New master edge
                self._add_edges_from_subshells_names(e_shells=e_shells)
            elif self.GOS == 'hydrogenic':
                # The hydrogenic GOS includes all the L subshells in one
                # so we get rid of the others
                e_shells.pop()
            else:
                # Add the other subshells of the same element
                # and couple their intensity and onset_energy to that of the
                # master edge
                edge = EELSCLEdge(e_shells.pop(), GOS=self.GOS)

                edge.intensity.twin = master_edge.intensity
                delta = _give_me_delta(master_edge.GOS.onset_energy,
                                       edge.GOS.onset_energy)
                idelta = _give_me_idelta(master_edge.GOS.onset_energy,
                                         edge.GOS.onset_energy)
                edge.onset_energy.twin_function = delta
                edge.onset_energy.twin_inverse_function = idelta
                edge.onset_energy.twin = master_edge.onset_energy
                edge.free_onset_energy = False
                self.append(edge)
                if copy2interactive_ns is True:
                    interactive_ns[edge.name] = edge
                    interactive_ns[element].append(edge)

    def resolve_fine_structure(
            self, preedge_safe_window_width=preferences.EELS.preedge_safe_window_width, i1=0):
        """Adjust the fine structure of all edges to avoid overlapping

        This function is called automatically everytime the position of an edge
        changes

        Parameters
        ----------
        preedge_safe_window_width : float
            minimum distance between the fine structure of an ionization edge
            and that of the following one.
        """

        if self._suspend_auto_fine_structure_width is True:
            return

        if not self._active_edges:
            return

        while (self._active_edges[i1].fine_structure_active is False and
               i1 < len(self._active_edges) - 1):
            i1 += 1
        if i1 < len(self._active_edges) - 1:
            i2 = i1 + 1
            while (self._active_edges[i2].fine_structure_active is False and
                    i2 < len(self._active_edges) - 1):
                i2 += 1
            if self._active_edges[i2].fine_structure_active is True:
                distance_between_edges = self._active_edges[i2].onset_energy.value - \
                    self._active_edges[i1].onset_energy.value
                if self._active_edges[i1].fine_structure_width > distance_between_edges - \
                        preedge_safe_window_width:
                    if (distance_between_edges -
                            preedge_safe_window_width) <= \
                            preferences.EELS.min_distance_between_edges_for_fine_structure:
                        print " Automatically desactivating the fine \
                        structure of edge number", i2 + 1, "to avoid conflicts\
                         with edge number", i1 + 1
                        self._active_edges[i2].fine_structure_active = False
                        self._active_edges[
                            i2].fine_structure_coeff.free = False
                        self.resolve_fine_structure(i1=i2)
                    else:
                        new_fine_structure_width = distance_between_edges - \
                            preedge_safe_window_width
                        print "Automatically changing the fine structure \
                        width of edge", i1 + 1, "from", \
                            self._active_edges[i1].fine_structure_width, "eV to", new_fine_structure_width, \
                            new_fine_structure_width, \
                            "eV to avoid conflicts with edge number", i2 + 1
                        self._active_edges[
                            i1].fine_structure_width = new_fine_structure_width
                        self.resolve_fine_structure(i1=i2)
                else:
                    self.resolve_fine_structure(i1=i2)
        else:
            return

    def fit(self, fitter=None, method='ls', grad=False,
            bounded=False, ext_bounding=False, update_plot=False,
            kind='std', **kwargs):
        """Fits the model to the experimental data

        Parameters
        ----------
        fitter : {None, "leastsq", "odr", "mpfit", "fmin"}
            The optimizer to perform the fitting. If None the fitter
            defined in the Preferences is used. leastsq is the most
            stable but it does not support bounding. mpfit supports
            bounding. fmin is the only one that supports
            maximum likelihood estimation, but it is less robust than
            the Levenberg–Marquardt based leastsq and mpfit, and it is
            better to use it after one of them to refine the estimation.
        method : {'ls', 'ml'}
            Choose 'ls' (default) for least squares and 'ml' for
            maximum-likelihood estimation. The latter only works with
            fitter = 'fmin'.
        grad : bool
            If True, the analytical gradient is used if defined to
            speed up the estimation.
        ext_bounding : bool
            If True, enforce bounding by keeping the value of the
            parameters constant out of the defined bounding area.
        bounded : bool
            If True performs bounded optimization if the fitter
            supports it. Currently only mpfit support bounding.
        update_plot : bool
            If True, the plot is updated during the optimization
            process. It slows down the optimization but it permits
            to visualize the optimization evolution.
        kind : {'std', 'smart'}
            If 'std' (default) performs standard fit. If 'smart'
            performs smart_fit

        **kwargs : key word arguments
            Any extra key word argument will be passed to the chosen
            fitter

        See Also
        --------
        multifit, smart_fit

        """
        if kind == 'smart':
            self.smart_fit(fitter=fitter,
                           method=method,
                           grad=grad,
                           bounded=bounded,
                           ext_bounding=ext_bounding,
                           update_plot=update_plot,
                           **kwargs)
        elif kind == 'std':
            Model.fit(self,
                      fitter=fitter,
                      method=method,
                      grad=grad,
                      bounded=bounded,
                      ext_bounding=ext_bounding,
                      update_plot=update_plot,
                      **kwargs)
        else:
            raise ValueError('kind must be either \'std\' or \'smart\'.'
                             '\'%s\' provided.' % kind)

    def smart_fit(self, start_energy=None, **kwargs):
        """ Fits everything in a cascade style.

        Parameters
        ----------

        start_energy : {float, None}
            If float, limit the range of energies from the left to the
            given value.
        **kwargs : key word arguments
            Any extra key word argument will be passed to
            the fit method. See the fit method documentation for
            a list of valid arguments.

        See Also
        --------
        fit, multifit

        """

        # Fit background
        self.fit_background(start_energy, **kwargs)

        # Fit the edges
        for i in xrange(0, len(self._active_edges)):
            self._fit_edge(i, start_energy, **kwargs)

    def _get_first_ionization_edge_energy(self, start_energy=None):
        """Calculate the first ionization edge energy.

        Returns
        -------
        iee : float or None
            The first ionization edge energy or None if no edge is defined in
            the model.

        """
        if not self._active_edges:
            return None
        start_energy = self._get_start_energy(start_energy)
        iee_list = [edge.onset_energy.value for edge in self._active_edges
                    if edge.onset_energy.value > start_energy]
        iee = min(iee_list) if iee_list else None
        return iee

    def _get_start_energy(self, start_energy=None):
        E0 = self.axis.axis[self.channel_switches][0]
        if not start_energy or start_energy < E0:
            start_energy = E0
        return start_energy

    def fit_background(self, start_energy=None, only_current=True, **kwargs):
        """Fit the background to the first active ionization edge
        in the energy range.

        Parameters
        ----------
        start_energy : {float, None}, optional
            If float, limit the range of energies from the left to the
            given value. Default None.
        only_current : bool, optional
            If True, only fit the background at the current coordinates.
            Default True.
        **kwargs : extra key word arguments
            All extra key word arguments are passed to fit or
            multifit.

        """

        # If there is no active background compenent do nothing
        if not self._active_background_components:
            return
        iee = self._get_first_ionization_edge_energy(start_energy=start_energy)
        if iee is not None:
            to_disable = [edge for edge in self._active_edges
                          if edge.onset_energy.value >= iee]
            E2 = iee - preferences.EELS.preedge_safe_window_width
            self.disable_edges(to_disable)
        else:
            E2 = None
        self.set_signal_range(start_energy, E2)
        if only_current:
            self.fit(**kwargs)
        else:
            self.multifit(**kwargs)
        self.channel_switches = copy.copy(self.backup_channel_switches)
        if iee is not None:
            self.enable_edges(to_disable)

    def two_area_background_estimation(self, E1=None, E2=None, powerlaw=None):
        """Estimates the parameters of a power law background with the two
        area method.

        Parameters
        ----------
        E1 : float
        E2 : float
        powerlaw : PowerLaw component or None
            If None, it will try to guess the right component from the
            background components of the model

        """
        if powerlaw is None:
            for component in self._active_background_components:
                if isinstance(component, components.PowerLaw):
                    if powerlaw is None:
                        powerlaw = component
                    else:
                        messages.warning(
                            'There are more than two power law '
                            'background components defined in this model, '
                            'please use the powerlaw keyword to specify one'
                            ' of them')
                        return
                else:  # No power law component
                    return

        ea = self.axis.axis[self.channel_switches]
        E1 = self._get_start_energy(E1)
        if E2 is None:
            E2 = self._get_first_ionization_edge_energy(start_energy=E1)
            if E2 is None:
                E2 = ea[-1]
            else:
                E2 = E2 - \
                    preferences.EELS.preedge_safe_window_width

        if not powerlaw.estimate_parameters(
                self.spectrum, E1, E2, only_current=False):
            messages.warning(
                "The power law background parameters could not "
                "be estimated.\n"
                "Try choosing a different energy range for the estimation")
            return

    def _fit_edge(self, edgenumber, start_energy=None, **kwargs):
        backup_channel_switches = self.channel_switches.copy()
        ea = self.axis.axis[self.channel_switches]
        if start_energy is None:
            start_energy = ea[0]
        preedge_safe_window_width = preferences.EELS.preedge_safe_window_width
        # Declare variables
        edge = self._active_edges[edgenumber]
        if edge.intensity.twin is not None or edge.active is False or \
                edge.onset_energy.value < start_energy or edge.onset_energy.value > ea[-1]:
            return 1
        #~print "Fitting edge ", edge.name
        last_index = len(self._active_edges) - 1
        i = 1
        twins = []
        #~print "Last edge index", last_index
        while edgenumber + i <= last_index and (
                self._active_edges[edgenumber + i].intensity.twin is not None or
                self._active_edges[edgenumber + i].active is False):
            if self._active_edges[edgenumber + i].intensity.twin is not None:
                twins.append(self._active_edges[edgenumber + i])
            i += 1
        #~print "twins", twins
        #~print "next_edge_index", edgenumber + i
        if (edgenumber + i) > last_index:
            nextedgeenergy = ea[-1]
        else:
            nextedgeenergy = self._active_edges[edgenumber + i].onset_energy.value - \
                preedge_safe_window_width

        # Backup the fsstate
        to_activate_fs = []
        for edge_ in [edge, ] + twins:
            if edge_.fine_structure_active is True and edge_.fine_structure_coeff.free is True:
                to_activate_fs.append(edge_)
        self.disable_fine_structure(to_activate_fs)

        # Smart Fitting

        #~print("Fitting region: %s-%s" % (start_energy,nextedgeenergy))

        # Without fine structure to determine onset_energy
        edges_to_activate = []
        for edge_ in self._active_edges[edgenumber + 1:]:
            if edge_.active is True and edge_.onset_energy.value >= nextedgeenergy:
                edge_.active = False
                edges_to_activate.append(edge_)
        #~print "edges_to_activate", edges_to_activate
        #~print "Fine structure to fit", to_activate_fs

        self.set_signal_range(start_energy, nextedgeenergy)
        if edge.free_onset_energy is True:
            #~print "Fit without fine structure, onset_energy free"
            edge.onset_energy.free = True
            self.fit(**kwargs)
            edge.onset_energy.free = False
            print "onset_energy = ", edge.onset_energy.value
            self._touch()
        elif edge.intensity.free is True:
            #~print "Fit without fine structure"
            self.enable_fine_structure(to_activate_fs)
            self.remove_fine_structure_data(to_activate_fs)
            self.disable_fine_structure(to_activate_fs)
            self.fit(**kwargs)

        if len(to_activate_fs) > 0:
            self.set_signal_range(start_energy, nextedgeenergy)
            self.enable_fine_structure(to_activate_fs)
            #~print "Fit with fine structure"
            self.fit(**kwargs)

        self.enable_edges(edges_to_activate)
        # Recover the channel_switches. Remove it or make it smarter.
        self.channel_switches = backup_channel_switches

    def quantify(self):
        """Prints the value of the intensity of all the independent
        active EELS core loss edges defined in the model

        """
        elements = {}
        for edge in self._active_edges:
            if edge.active and edge.intensity.twin is None:
                element = edge.element
                subshell = edge.subshell
                if element not in elements:
                    elements[element] = {}
                elements[element][subshell] = edge.intensity.value
        print
        print "Absolute quantification:"
        print "Elem.\tIntensity"
        for element in elements:
            if len(elements[element]) == 1:
                for subshell in elements[element]:
                    print "%s\t%f" % (
                        element, elements[element][subshell])
            else:
                for subshell in elements[element]:
                    print "%s_%s\t%f" % (element, subshell,
                                         elements[element][subshell])

    def remove_fine_structure_data(self, edges_list=None):
        """Remove the fine structure data from the fitting routine as
        defined in the fine_structure_width parameter of the component.EELSCLEdge

        Parameters
        ----------
        edges_list : None or  list of EELSCLEdge or list of edge names
            If None, the operation is performed on all the edges in the model.
            Otherwise, it will be performed only on the listed components.

        See Also
        --------
        enable_edges, disable_edges, enable_background,
        disable_background, enable_fine_structure,
        disable_fine_structure, set_all_edges_intensities_positive,
        unset_all_edges_intensities_positive, enable_free_onset_energy,
        disable_free_onset_energy, fix_edges, free_edges, fix_fine_structure,
        free_fine_structure

        """
        if edges_list is None:
            edges_list = self._active_edges
        else:
            edges_list = [self._get_component(x) for x in edges_list]
        for edge in edges_list:
            if edge.isbackground is False and edge.fine_structure_active is True:
                start = edge.onset_energy.value
                stop = start + edge.fine_structure_width
                self.remove_signal_range(start, stop)

    def enable_edges(self, edges_list=None):
        """Enable the edges listed in edges_list. If edges_list is
        None (default) all the edges with onset in the spectrum energy
        region will be enabled.

        Parameters
        ----------
        edges_list : None or  list of EELSCLEdge or list of edge names
            If None, the operation is performed on all the edges in the model.
            Otherwise, it will be performed only on the listed components.

        See Also
        --------
        enable_edges, disable_edges, enable_background,
        disable_background, enable_fine_structure,
        disable_fine_structure, set_all_edges_intensities_positive,
        unset_all_edges_intensities_positive, enable_free_onset_energy,
        disable_free_onset_energy, fix_edges, free_edges, fix_fine_structure,
        free_fine_structure

        """

        if edges_list is None:
            edges_list = self.edges
        else:
            edges_list = [self._get_component(x) for x in edges_list]
        for edge in edges_list:
            if edge.isbackground is False:
                edge.active = True
        self.resolve_fine_structure()

    def disable_edges(self, edges_list=None):
        """Disable the edges listed in edges_list. If edges_list is None (default)
        all the edges with onset in the spectrum energy region will be
        disabled.

        Parameters
        ----------
        edges_list : None or  list of EELSCLEdge or list of edge names
            If None, the operation is performed on all the edges in the model.
            Otherwise, it will be performed only on the listed components.

        See Also
        --------
        enable_edges, disable_edges, enable_background,
        disable_background, enable_fine_structure,
        disable_fine_structure, set_all_edges_intensities_positive,
        unset_all_edges_intensities_positive, enable_free_onset_energy,
        disable_free_onset_energy, fix_edges, free_edges, fix_fine_structure,
        free_fine_structure

        """
        if edges_list is None:
            edges_list = self._active_edges
        else:
            edges_list = [self._get_component(x) for x in edges_list]
        for edge in edges_list:
            if edge.isbackground is False:
                edge.active = False
        self.resolve_fine_structure()

    def enable_background(self):
        """Enable the background componets.

        """
        for component in self._background_components:
            component.active = True

    def disable_background(self):
        """Disable the background components.

        """
        for component in self._active_background_components:
            component.active = False

    def enable_fine_structure(self, edges_list=None):
        """Enable the fine structure of the edges listed in edges_list.
        If edges_list is None (default) the fine structure of all the edges
        with onset in the spectrum energy region will be enabled.

        Parameters
        ----------
        edges_list : None or  list of EELSCLEdge or list of edge names
            If None, the operation is performed on all the edges in the model.
            Otherwise, it will be performed only on the listed components.

        See Also
        --------
        enable_edges, disable_edges, enable_background,
        disable_background, enable_fine_structure,
        disable_fine_structure, set_all_edges_intensities_positive,
        unset_all_edges_intensities_positive, enable_free_onset_energy,
        disable_free_onset_energy, fix_edges, free_edges, fix_fine_structure,
        free_fine_structure

        """
        if edges_list is None:
            edges_list = self._active_edges
        else:
            edges_list = [self._get_component(x) for x in edges_list]
        for edge in edges_list:
            if edge.isbackground is False:
                edge.fine_structure_active = True
                edge.fine_structure_coeff.free = True
        self.resolve_fine_structure()

    def disable_fine_structure(self, edges_list=None):
        """Disable the fine structure of the edges listed in edges_list.
        If edges_list is None (default) the fine structure of all the edges
        with onset in the spectrum energy region will be disabled.

        Parameters
        ----------
        edges_list : None or  list of EELSCLEdge or list of edge names
            If None, the operation is performed on all the edges in the model.
            Otherwise, it will be performed only on the listed components.

        See Also
        --------
        enable_edges, disable_edges, enable_background,
        disable_background, enable_fine_structure,
        disable_fine_structure, set_all_edges_intensities_positive,
        unset_all_edges_intensities_positive, enable_free_onset_energy,
        disable_free_onset_energy, fix_edges, free_edges, fix_fine_structure,
        free_fine_structure

        """
        if edges_list is None:
            edges_list = self._active_edges
        else:
            edges_list = [self._get_component(x) for x in edges_list]
        for edge in edges_list:
            if edge.isbackground is False:
                edge.fine_structure_active = False
                edge.fine_structure_coeff.free = False
        self.resolve_fine_structure()

    def set_all_edges_intensities_positive(self):
        for edge in self._active_edges:
            edge.intensity.ext_force_positive = True
            edge.intensity.ext_bounded = True

    def unset_all_edges_intensities_positive(self):
        for edge in self._active_edges:
            edge.intensity.ext_force_positive = False
            edge.intensity.ext_bounded = False

    def enable_free_onset_energy(self, edges_list=None):
        """Enable the automatic freeing of the onset_energy parameter during a
        smart fit for the edges listed in edges_list.
        If edges_list is None (default) the onset_energy of all the edges
        with onset in the spectrum energy region will be freeed.

        Parameters
        ----------
        edges_list : None or  list of EELSCLEdge or list of edge names
            If None, the operation is performed on all the edges in the model.
            Otherwise, it will be performed only on the listed components.

        See Also
        --------
        enable_edges, disable_edges, enable_background,
        disable_background, enable_fine_structure,
        disable_fine_structure, set_all_edges_intensities_positive,
        unset_all_edges_intensities_positive, enable_free_onset_energy,
        disable_free_onset_energy, fix_edges, free_edges, fix_fine_structure,
        free_fine_structure

        """
        if edges_list is None:
            edges_list = self._active_edges
        else:
            edges_list = [self._get_component(x) for x in edges_list]
        for edge in edges_list:
            if edge.isbackground is False:
                edge.free_onset_energy = True

    def disable_free_onset_energy(self, edges_list=None):
        """Disable the automatic freeing of the onset_energy parameter during a
        smart fit for the edges listed in edges_list.
        If edges_list is None (default) the onset_energy of all the edges
        with onset in the spectrum energy region will not be freed.
        Note that if their atribute edge.onset_energy.free is True, the parameter
        will be free during the smart fit.

        Parameters
        ----------
        edges_list : None or  list of EELSCLEdge or list of edge names
            If None, the operation is performed on all the edges in the model.
            Otherwise, it will be performed only on the listed components.

        See Also
        --------
        enable_edges, disable_edges, enable_background,
        disable_background, enable_fine_structure,
        disable_fine_structure, set_all_edges_intensities_positive,
        unset_all_edges_intensities_positive, enable_free_onset_energy,
        disable_free_onset_energy, fix_edges, free_edges, fix_fine_structure,
        free_fine_structure

        """

        if edges_list is None:
            edges_list = self._active_edges
        else:
            edges_list = [self._get_component(x) for x in edges_list]
        for edge in edges_list:
            if edge.isbackground is False:
                edge.free_onset_energy = True

    def fix_edges(self, edges_list=None):
        """Fixes all the parameters of the edges given in edges_list.
        If edges_list is None (default) all the edges will be fixed.

        Parameters
        ----------
        edges_list : None or  list of EELSCLEdge or list of edge names
            If None, the operation is performed on all the edges in the model.
            Otherwise, it will be performed only on the listed components.

        See Also
        --------
        enable_edges, disable_edges, enable_background,
        disable_background, enable_fine_structure,
        disable_fine_structure, set_all_edges_intensities_positive,
        unset_all_edges_intensities_positive, enable_free_onset_energy,
        disable_free_onset_energy, fix_edges, free_edges, fix_fine_structure,
        free_fine_structure

        """
        if edges_list is None:
            edges_list = self._active_edges
        else:
            edges_list = [self._get_component(x) for x in edges_list]
        for edge in edges_list:
            if edge.isbackground is False:
                edge.intensity.free = False
                edge.onset_energy.free = False
                edge.fine_structure_coeff.free = False

    def free_edges(self, edges_list=None):
        """Frees all the parameters of the edges given in edges_list.
        If edges_list is None (default) all the edges will be freeed.

        Parameters
        ----------
        edges_list : None or  list of EELSCLEdge or list of edge names
            If None, the operation is performed on all the edges in the model.
            Otherwise, it will be performed only on the listed components.

        See Also
        --------
        enable_edges, disable_edges, enable_background,
        disable_background, enable_fine_structure,
        disable_fine_structure, set_all_edges_intensities_positive,
        unset_all_edges_intensities_positive, enable_free_onset_energy,
        disable_free_onset_energy, fix_edges, free_edges, fix_fine_structure,
        free_fine_structure

        """

        if edges_list is None:
            edges_list = self._active_edges
        else:
            edges_list = [self._get_component(x) for x in edges_list]
        for edge in edges_list:
            if edge.isbackground is False:
                edge.intensity.free = True
                #edge.onset_energy.free = True
                #edge.fine_structure_coeff.free = True

    def fix_fine_structure(self, edges_list=None):
        """Fixes all the parameters of the edges given in edges_list.
        If edges_list is None (default) all the edges will be fixed.

        Parameters
        ----------
        edges_list : None or  list of EELSCLEdge or list of edge names
            If None, the operation is performed on all the edges in the model.
            Otherwise, it will be performed only on the listed components.

        See Also
        --------
        enable_edges, disable_edges, enable_background,
        disable_background, enable_fine_structure,
        disable_fine_structure, set_all_edges_intensities_positive,
        unset_all_edges_intensities_positive, enable_free_onset_energy,
        disable_free_onset_energy, fix_edges, free_edges, fix_fine_structure,
        free_fine_structure

        """
        if edges_list is None:
            edges_list = self._active_edges
        else:
            edges_list = [self._get_component(x) for x in edges_list]
        for edge in edges_list:
            if edge.isbackground is False:
                edge.fine_structure_coeff.free = False

    def free_fine_structure(self, edges_list=None):
        """Frees all the parameters of the edges given in edges_list.
        If edges_list is None (default) all the edges will be freeed.

        Parameters
        ----------
        edges_list : None or  list of EELSCLEdge or list of edge names
            If None, the operation is performed on all the edges in the model.
            Otherwise, it will be performed only on the listed components.

        See Also
        --------
        enable_edges, disable_edges, enable_background,
        disable_background, enable_fine_structure,
        disable_fine_structure, set_all_edges_intensities_positive,
        unset_all_edges_intensities_positive, enable_free_onset_energy,
        disable_free_onset_energy, fix_edges, free_edges, fix_fine_structure,
        free_fine_structure

        """
        if edges_list is None:
            edges_list = self._active_edges
        else:
            edges_list = [self._get_component(x) for x in edges_list]
        for edge in edges_list:
            if edge.isbackground is False:
                edge.fine_structure_coeff.free = True

    def suspend_auto_fine_structure_width(self):
        """Disable the automatic adjustament of the core-loss edges fine
        structure width.

        See Also
        --------
        resume_update
        update_plot

        """
        if self._suspend_auto_fine_structure_width is False:
            self._suspend_auto_fine_structure_width = True
        else:
            warnings.warn("Already suspended, does nothing.")

    def resume_auto_fine_structure_width(self, update=True):
        """Enable the automatic adjustament of the core-loss edges fine
        structure width.

        Parameters
        ----------
        update : bool, optional
            If True, also execute the automatic adjustment (default).


        See Also
        --------
        suspend_update
        update_plot

        """
        if self._suspend_auto_fine_structure_width is True:
            self._suspend_auto_fine_structure_width = False
            if update is True:
                self.resolve_fine_structure()
        else:
            warnings.warn("Not suspended, nothing to resume.")
