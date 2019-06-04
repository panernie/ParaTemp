"""This file defines a class useful for setting up simulations"""

########################################################################
#                                                                      #
# This test was written by Thomas Heavey in 2019.                      #
#        theavey@bu.edu     thomasjheavey@gmail.com                    #
#                                                                      #
# Copyright 2017-19 Thomas J. Heavey IV                                #
#                                                                      #
# Licensed under the Apache License, Version 2.0 (the "License");      #
# you may not use this file except in compliance with the License.     #
# You may obtain a copy of the License at                              #
#                                                                      #
#    http://www.apache.org/licenses/LICENSE-2.0                        #
#                                                                      #
# Unless required by applicable law or agreed to in writing, software  #
# distributed under the License is distributed on an "AS IS" BASIS,    #
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or      #
# implied.                                                             #
# See the License for the specific language governing permissions and  #
# limitations under the License.                                       #
#                                                                      #
########################################################################

from collections import OrderedDict
import logging
import pathlib
import re
import sys
import typing

import gromacs

from .molecule import Molecule
from .system import System
from ..tools import cd


__all__ = ['Simulation', 'SimpleSimulation']


log = logging.getLogger(__name__)
if not log.hasHandlers():
    level = logging.INFO
    log.setLevel(level)
    handler = logging.StreamHandler()
    handler.setLevel(level)
    formatter = logging.Formatter('%(asctime)s - %(name)s - '
                                  '%(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    log.addHandler(handler)


GenPath = typing.Union[pathlib.Path, str]


if sys.version_info >= (3, 6):
    def resolve_path(path):
        return pathlib.Path(path).resolve()
else:
    def resolve_path(path):
        try:
            return pathlib.Path(path).resolve()
        except FileNotFoundError:
            pass
        path = pathlib.Path(path)
        if path.is_absolute():
            return path
        return pathlib.Path.cwd().joinpath(path)


class Simulation(object):
    """
    A class for setting up and running GROMACS simulations
    """

    _fp = staticmethod(resolve_path)

    def __init__(self, name: str, gro: GenPath,
                 top: GenPath, base_folder: GenPath = '.',
                 mdps: dict = None):
        self.name = name
        self.top = self._fp(top)
        self.geometries = OrderedDict(initial=self._fp(gro))
        self.base_folder = self._fp(base_folder)
        self.folders = dict(base=self.base_folder)
        self.mdps = dict() if mdps is None else mdps
        self.tprs = dict()
        self.deffnms = dict()
        self.outputs = dict()
        for mdp in self.mdps:
            setattr(self, mdp, self._make_step_method(mdp))
            self.mdps[mdp] = self._fp(mdps[mdp])

    @property
    def last_geometry(self) -> pathlib.Path:
        """
        The path to the output geometry from the most recent simulation

        :return: The path to the last output geometry
        """
        return next(reversed(self.geometries.items()))[1]

    @property
    def _next_folder_index(self) -> int:
        """
        Index for next folder to be created

        Note, this will not work if there are 99 or more folders.
        Folders should be of the form '01-minimize-benzene'
        :return: next folder index
        :rtype: int
        """
        folders = [d.name for d in self.base_folder.iterdir() if d.is_dir()]
        nums = [int(d[:2]) for d in folders if re.match(r'\d{2}-\w+-\w+', d)]
        nums.sort()
        return nums[-1]+1 if nums else 1

    def _make_step_method(self, step_name: str) -> typing.Callable:
        """
        Make a function that runs a GROMACS "step" (minimization, equil, etc.)

        :param str step_name: Name of the step. This should be a valid key to
            `mdps` dict and will be the name of the method to which this
            function is mapped.
        :return: A function to run the step specified by the mdp
        :rtype: typing.Callable
        """
        def func(geometry=None):
            geometry = self.last_geometry if geometry is None else geometry
            folder_index = self._next_folder_index
            folder = self.base_folder / '{:0>2}-{}-{}'.format(folder_index,
                                                              step_name,
                                                              self.name)
            folder.mkdir()
            self.folders[step_name] = folder
            with cd(folder):
                tpr = self._compile_tpr(step_name, geometry)
                self._run_mdrun(step_name, tpr)
            return folder
        return func

    def _compile_tpr(self, step_name: str,
                     geometry: GenPath = None,
                     trajectory: GenPath = None
                     ) -> pathlib.Path:
        """
        Make a tpr file for the chosen step_name and associated mdp file

        :param step_name: Key for the mdp file from the dict mdps
        :param geometry: Path to the geometry to be used as input. If None,
            :attr:`last_geometry` will be used.
        :param trajectory: Path to a trajectory file from which to take the
            input geometry. This is useful when a full precision geometry is
            needed as input and a trr file can be used. If None,
            no trajectory will be given to grompp.
        :return: The Path to the tpr file
        """
        geometry = self.last_geometry if geometry is None else geometry
        tpr = '{}-{}.tpr'.format(self.name, step_name)
        p_tpr = self._fp(tpr)
        self.tprs[step_name] = p_tpr
        rc, output, junk = gromacs.grompp(c=geometry,
                                          p=self.top,
                                          f=self.mdps[step_name],
                                          o=tpr,
                                          t=trajectory,
                                          stdout=False)
        # Doesn't capture output if failed?
        self.outputs['compile_{}'.format(step_name)] = output
        return p_tpr

    def _run_mdrun(self, step_name: str, tpr: GenPath = None
                   ) -> pathlib.Path:
        """
        Run mdrun with the given step_name or explicitly given tpr file.

        :param step_name: The name of this step
        :param tpr: Path to the tpr file. If None, the tpr will be found
            from the dict :attr:`tprs` with the key being `step_name`
        :return: The Path to the output geometry
        """
        tpr = self.tprs[step_name] if tpr is None else tpr
        deffnm = '{}-{}-out'.format(self.name, step_name)
        p_deffnm = self._fp(deffnm)
        self.deffnms[step_name] = p_deffnm
        rc, output, junk = gromacs.mdrun(s=tpr, deffnm=deffnm, stdout=False)
        # Doesn't capture output if failed?
        self.outputs['run_{}'.format(step_name)] = output
        gro = p_deffnm.with_suffix('.gro')
        self.geometries[step_name] = gro
        return gro


_type_mol_inputs = typing.Union[str, typing.List[typing.Union[dict,
                                                              Molecule]]]


class SimpleSimulation(object):
    """
    SimpleSimulation can be used to easily setup a Simulation with many defaults
    """

    def __init__(self, name: str,
                 mol_inputs: _type_mol_inputs = 'ask',
                 solvent_dielectric: float = 9.1  # DCM
                 ):
        log.info('Instantiating a SimpleSimulation named {}'.format(name))
        self.name = name
        self.molecules = list()  # type: typing.List[Molecule]
        self._process_mol_inputs(mol_inputs)
        self._dielectric = solvent_dielectric
        self.directories = dict()  # type: typing.Dict[str, pathlib.Path]
        self.system = None  # type: System
        self._SimClass = Simulation
        self.simulation = None  # type: Simulation

    def _process_mol_inputs(self, mol_inputs):
        if mol_inputs == 'ask':
            more = True
            while more:
                self.molecules.append(Molecule.assisted())
                more = (True if 'y' in input('Any more molecules? [yn]').lower()
                        else False)
        elif isinstance(mol_inputs, typing.Sequence):
            if isinstance(mol_inputs[0], Molecule):
                self.molecules = mol_inputs
            else:
                for mol in mol_inputs:
                    self.molecules.append(Molecule.from_make_mol_inputs(mol))
        elif isinstance(mol_inputs, Molecule):
            self.molecules = [mol_inputs]
        else:
            try:
                self.molecules = Molecule.from_make_mol_inputs(mol_inputs)
            except KeyError:  # maybe other Errors?
                raise ValueError('Unrecognized input: {}'.format(mol_inputs))
        dirs = {'molecule_{}'.format(mol.name): mol.directory for mol
                in self.molecules}
        self.directories.update(dirs)

    def parameterize(self):
        log.info('Parameterizing the {} Molecules'.format(len(self.molecules)))
        for mol in self.molecules:
            mol.parameterize()

    def combine(self):
        """
        Combining all molecules into a given System

        :return: None
        """
        log.info('Combining the {} Molecules into a single System'.format(
            len(self.molecules)))
        self.system = System(*self.molecules,
                             name=self.name,
                             shift=True,
                             spacing=2.0,
                             include_gbsa=True)
        self.directories['system'] = self.system.directory

    def make_simulation(self, mdps: dict = None):
        """
        Making a Simulation object from the System

        :param dict mdps: dict of step names to strings of path to existing
            mdp files
        :return: None
        """
        log.info('Creating a Simulation object from the {} '
                 'System object'.format(self.system.name))
        self.directories['simulation_base'] = self.system.directory
        _mdps = {'minimize': 'path/to/mdp',
                 'equilibrate': 'path/to/mdp'}
        if mdps is not None:
            _mdps.update(mdps)
        self.simulation = self._SimClass(
            name=self.name,
            gro=self.system.gro_path,
            top=self.system.top_path,
            base_folder=self.system.directory
        )

    def _insert_dielectric(self, mdps: dict) -> typing.Dict[str, str]:
        """
        Use Python format ({}) to dielectric constant into given mdp files

        :param dict mdps: dict of step names to strings of path to existing
            mdp files
        :return: dict of step names to strings ot paths to edited mdp files
            (now in a folder specific to this simulation)
        """
        _dir = self.directories['simulation_base']
        d_out = dict()
        for key in mdps:
            old_path = pathlib.Path(mdps[key])
            new_path = _dir / old_path.name
            text = old_path.read_text()
            text = text.format(dielectric=self._dielectric)
            new_path.write_text(text)
            log.info('wrote {} mdp with dielectric replaced to {}'.format(
                key, new_path))
            d_out[key] = str(new_path)
        return d_out
