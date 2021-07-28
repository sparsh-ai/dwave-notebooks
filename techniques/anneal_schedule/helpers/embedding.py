# coding: utf-8
# Copyright 2020 D-Wave Systems Inc.
#
#    Licensed under the Apache License, Version 2.0 (the "License");
#    you may not use this file except in compliance with the License.
#    You may obtain a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS,
#    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#    See the License for the specific language governing permissions and
#    limitations under the License.
#
# =============================================================================
"""Composite that directly embeds small problems defined on qubit indices onto
a QPU's working graph.

THIS IS A TEMPORARY WORKAROUND! 

This DirectEmbeddingComposite class should be replaced with an Ocean tool; for 
example, dwave-system's TilingComposite once that is updated to support Pegasus.

"""
import dwave_networkx as dnx
import dimod
from dimod.exceptions import BinaryQuadraticModelStructureError
from dwave_networkx import chimera_graph, pegasus_graph
from dwave.system.composites.embedding import FixedEmbeddingComposite
from dwave.embedding.exceptions import EmbeddingError

class DirectEmbeddingComposite(dimod.ComposedSampler):
    """
    Directly embeds small problems to horizontally adjacent Chimera unit cell(s).
    Maps small problem graphs, defined on the indexed couplers of a logical
    Chimera unit cell, or several in a row, to physical :math:`K_{4,4}` unit cells
    of a Pegasus or Chimera QPU.
    Nodes of a Chimera unit cell are indexed :math:`0` to :math:'7', with
    :math:`0-3` representing one shore, implemented on the QPU by vertically
    oriented qubits, and :math:`4-7` the second shore, of horizontally oriented
    qubits. Rows of Chimera unit cells are linked by couplers between the
    horizontal qubits; for example, couplers :math:`(4, 12), (5, 13)...` between
    the leftmost two unit cells.
    Args:
        child_sampler (:class:`dimod.Sampler`):
            A dimod sampler, such as a :obj:`.DWaveSampler`, that accepts
            only binary quadratic models of a particular structure: Chimera or
            Pegasus topology.
    Returns:
        :obj:`dimod.SampleSet`
    Examples:
       This example embeds an Ising model with a graph defined on two Chimera
       unit cells to a QPU. In this case, an Advantage system with
       Pegasus toplogy is selected.
       >>> from dwave.system import DWaveSampler, DirectEmbeddingComposite
       ...
       >>> qpu = DWaveSampler(solver={'QPU': True})
       >>> qpu.solver.properties["topology"]["type"] # doctest: +SKIP
       'pegasus'
       >>> sampler = DirectEmbeddingComposite(qpu)
       >>> h = {}
       >>> J = {(0, 4): 1, (1, 4): 1, (1, 6): 1, (6, 14): -1, (14, 11): 1, (11, 15): 1}
       >>> sampleset = sampler.sample_ising(h, J, num_reads=100)
       >>> sampleset.info["embedding_context"]["embedding"]   # doctest: +SKIP
       {0: [20], 1: [25], 4: [440], 6: [450], 11: [95], 14: [451], 15: [456]}
    Note:
       direct embedding is useful when it's important that each node be represented
       by a single qubit. For a Chimera QPU especially, direct embedding is often
       simply done by setting the desired node biases in the submission of a
       :class:`~dwave.system.samplers.DWaveSampler()` for example,
       >>> samplset = DWaveSampler().sample_ising({}, {(0, 4): 1})  # doctest: +SKIP
       You can shift embedding to another unit cell, :math: `n`, just by adding
       an offset of :math:`8*n` to the zero node. But if you want the emebdding
       to be agnostic to the selected QPU's working graph and topology, this
       composite can be useful.
    """
    def __init__(self, child_sampler):

        self.children = [child_sampler]

        # set the parameters
        self.parameters = parameters = child_sampler.parameters.copy()

        # set the properties
        self.properties = dict(child_properties=child_sampler.properties.copy())

        if self.properties["child_properties"]["category"] != "qpu":
            raise TypeError("Child sampler must be a QPU solver.")

        self.tiles = self.properties["child_properties"]["topology"]["shape"][0]
        self.topology_type = self.properties["child_properties"]["topology"]["type"]

    parameters = None  # overwritten by init
    children = None  # overwritten by init
    properties = None  # overwritten by init

    def sample_qubo(self, Q, **parameters):
        """
        Sample the binary quadratic model.
        Args:
            bqm (:obj:`dimod.BinaryQuadraticModel`):
                Binary quadratic model to be sampled from.
        Returns:
            :obj:`dimod.SampleSet`
        Examples:
           This example embeds a QUBO with a graph defined on two Chimera
           unit cells to a QPU. In this case, an Advantage system with
           Pegasus toplogy is selected.
           >>> from dwave.system import DWaveSampler, DirectEmbeddingComposite
           ...
           >>> qpu = DWaveSampler(solver={'QPU': True})
           >>> qpu.solver.properties["topology"]["type"] # doctest: +SKIP
           'pegasus'
           >>> sampler = DirectEmbeddingComposite(qpu)
           >>> Q = {(0, 0): -2.0, (1, 1): -4.0, (4, 4): -4.0, (6, 6): 0.0, (11, 11): -4.0,
           ...      (14, 14): 0.0, (15, 15): -2.0, (0, 4): 4.0, (1, 4): 4.0,
           ...      (1, 6): 4.0, (6, 14): -4.0, (14, 11): 4.0, (11, 15): 4.0}
           >>> sampleset = sampler.sample_qubo(Q, num_reads=100)
           >>> sampleset.info["embedding_context"]["embedding"]   # doctest: +SKIP
           {0: [20], 1: [25], 4: [440], 6: [450], 11: [95], 14: [451], 15: [456]}
        """

        if self.topology_type == "pegasus":
            self.couplers = dnx.pegasus_graph(self.tiles,
                         node_list=self.child.nodelist,
                         edge_list=self.child.edgelist).edges
            embed = self._pegasus_embedding
        else:
            self.couplers = self.child.edgelist
            embed = self._chimera_embedding

        max_nodes = 8*self.tiles if self.topology_type == "chimera" else \
                    3*8*self.tiles  #Rough (and extreme) limit on size
        if not set(val for tup in Q.keys() for val in tup).issubset(
               set(range(max_nodes))):
           msg = "Composite supports only indexed nodes"
           raise BinaryQuadraticModelStructureError(msg)

        uncoupled = (set(val for tup in Q.keys() for val in tup if tup[0] == tup[1]) -
                     set(val for tup in Q.keys() for val in tup if tup[0] != tup[1]))
        if uncoupled:
           msg = "Composite does not support singleton nodes: {}".format(uncoupled)
           raise BinaryQuadraticModelStructureError(msg)

        self.num_cells = max([max(u, v) for u,v in Q.keys()]) //8 + 1

        # Map to intra-cell and inter-cell couplers per unit cell
        self.edges_vh = list()
        self.edges_hh = list()
        for i in range(self.num_cells):
            self.edges_vh.append([(min(edge), max(edge))
                 for edge in Q.keys() if min(edge) != max(edge) and
                                         min(edge) >= 8 * i and
                                         max(edge) < 8 * (i + 1)])
            self.edges_hh.append([(min(edge), max(edge))
                 for edge in Q.keys() if min(edge) in range(8 * i, 8 * (i + 1)) and
                                         max(edge) in range(8 * (i + 1), 8 * (i + 2))])

        embedding = embed()

        return FixedEmbeddingComposite(self.child, embedding=embedding).sample_qubo(
                                           Q, **parameters)

    def _chimera_embedding(self):
        """Map problem's edges to qubits in row of unit cells in a Chimera QPU."""

        for row in range(self.tiles):
            for column in range(self.tiles - self.num_cells):

                q0 = 8*self.tiles*row + 8*column

                if all((set((v + q0, h + q0) for
                            v, h in self.edges_vh[i])
                       |  # Exploit empty self.edges_hh set for i = self.num_cells
                        set((v + q0, h + q0) for
                            v, h in self.edges_hh[i])
                       ).issubset(self.couplers) for i in range(self.num_cells)):

                       embedding = dict()
                       for i in range(self.num_cells):
                           embedding.update({v: [v + q0]
                                            for v, h in self.edges_vh[i]})
                           embedding.update({h: [h + q0]
                                            for v, h in self.edges_vh[i]})
                           embedding.update({h0: [h0 + q0]
                                            for h0, h1 in self.edges_hh[i]})
                           # Exploit empty self.edges_hh set for i = self.num_cells
                           embedding.update({h1: [h1 + q0]
                                            for h0, h1 in self.edges_hh[i]})

                       return (embedding)

        raise EmbeddingError("No embedding found")

    def _pegasus_embedding(self):
        """Map problem's edges to row of :math:`K_{4,4}` cells in a Pegaus QPU."""

        w, z, k_44 = self._scan_cells()

        embedding = dict()
        for i in range(self.num_cells):

            nodes_h = list(dnx.pegasus_coordinates(self.tiles).iter_pegasus_to_linear(
                           self._h_pair(w + i, z, k_44)))
            nodes_h1 = list(dnx.pegasus_coordinates(self.tiles).iter_pegasus_to_linear(
                            self._h_pair(w + i + 1, z, k_44)))

            embedding.update({v: [dnx.pegasus_coordinates(self.tiles).pegasus_to_linear(
                       (0, w + i, v - 8*i + 4*k_44, z))] for v, h in self.edges_vh[i]})
            embedding.update({h: [nodes_h[h - 8*i - 4]] for v, h in self.edges_vh[i]})
            embedding.update({h0: [nodes_h[h0 - 8*i - 4]] for
                              h0, h1 in self.edges_hh[i]})
            # Exploit empty self.edges_hh set for i = self.num_cells
            embedding.update({h1: [nodes_h1[h1 - 8*(i + 1) - 4]] for
                              h0, h1 in self.edges_hh[i]})

        return(embedding)

    def _scan_cells(self):
        """Find horizontally adjacent :math:`K_{4,4}` cells with needed couplers."""

        for z in range(self.tiles):
            for w in range(self.tiles - self.num_cells):
                for k_44 in range(3):

                    if all(self._couplers_vh(w, z, k_44, i) and
                           self._couplers_hh(w, z, k_44, i)
                              for i in range(self.num_cells)):
                        return (w, z, k_44)

        raise EmbeddingError("No embedding found")

    def _couplers_vh(self, w, z, k_44, cell):
        """Check internal couplers in a :math:`K_{4,4}` unit cell."""

        nodes_v = list(dnx.pegasus_coordinates(self.tiles).iter_pegasus_to_linear(
                       [(0, w + cell, k, z) for k in range(4*k_44, 4*k_44 + 4)]))
        nodes_h = list(dnx.pegasus_coordinates(self.tiles).iter_pegasus_to_linear(
                       self._h_pair(w + cell, z, k_44)))

        couplers_vh = [(nodes_v[v - 8*cell], nodes_h[h - 8*cell - 4]) for
                       v, h in self.edges_vh[cell]]

        return all(edge in self.couplers for edge in couplers_vh)

    def _couplers_hh(self, w, z, k_44, cell):
        """Check external couplers between horizontal shores of unit cells."""

        left = list(dnx.pegasus_coordinates(self.tiles).iter_pegasus_to_linear(
                    self._h_pair(w + cell, z, k_44)))
        right = list(dnx.pegasus_coordinates(self.tiles).iter_pegasus_to_linear(
                     self._h_pair(w + cell + 1, z, k_44)))

        couplers_hh = [(left[h0 - 8*cell - 4], right[h1 - 8*(cell + 1) - 4])
                       for h0, h1 in self.edges_hh[cell]]

        return all(edge in self.couplers for edge in couplers_hh)

    def _h_pair(self, w, z, k_44):
        """Get horizontal shore of a :math:`K_{4,4}` unit cell."""

        ww = z + 1 if k_44 > 0 else z
        zz = w - 1 if k_44 == 0 else w

        return [(1, ww, j, zz) for j in range(8 - 4*k_44, 12 - 4*k_44)]
