import numpy as np

from ..solvers.utils import SolverLookup, param_combinations
from ..solvers.solver_interface import ExitStatus

class ParameterTuner:

    def __init__(self, solvername, model, all_params=None, defaults=None):
        """
            Parameter tuner based on DeCaprio method [ref_to_decaprio]
            :param: solvername: Name of solver to tune
            :param: model: CPMpy model to tune parameters on
            :param: all_params: optional, dictionary with parameter names and values to tune. If None, use predefined parameter set.
        """
        self.solvername = solvername
        self.model = model
        self.all_params = all_params
        self.best_params = defaults
        if self.all_params is None:
            self.all_params = SolverLookup.lookup(solvername).tunable_params()
            self.best_params = SolverLookup.lookup(solvername).default_params()

        self._param_order = list(self.all_params.keys())
        self._best_config = self._params_to_np([self.best_params])

    def tune(self, time_limit=None, max_tries=None):
        # TODO: support time_limit

        # Init solver
        solver = SolverLookup.get(self.solvername, self.model)
        solver.solve(**self.best_params)

        best_runtime = solver.status().runtime

        # Add default's runtime as first entry in configs
        combos = list(param_combinations(self.all_params))
        combos_np = self._params_to_np(combos)

        # Ensure random start
        np.random.shuffle(combos_np)

        i = 0
        if max_tries is None:
            max_tries = len(combos_np)
        while len(combos_np) and i < max_tries:
            # Apply scoring to all combos
            scores = self._get_score(combos_np)
            max_idx = np.where(scores == scores.min())[0][0]
            # Get index of optimal combo
            params_np = combos_np[max_idx]
            # Remove optimal combo from combos
            combos_np = np.delete(combos_np, max_idx, axis=0)
            # Convert numpy array back to dictionary
            params_dict = self._np_to_params(params_np)

            # run solver
            solver.solve(**params_dict, time_limit=best_runtime)
            if (solver.status().exitstatus == ExitStatus.OPTIMAL or
                solver.status().exitstatus == ExitStatus.FEASIBLE) \
                    and  solver.status().runtime < best_runtime:
                best_runtime = solver.status().runtime
                # update surrogate
                self._best_config = params_np

            i += 1

        self.best_params = self._np_to_params(self._best_config)
        return self.best_params

    def _get_score(self, combos):
        """
            Return score for every parameter config in combos
        """
        mtrx = np.tile(self._best_config, len(combos)).reshape(combos.shape)
        return np.count_nonzero(combos != mtrx, axis=1)

    def _params_to_np(self,combos):
        arr = [[params[key] for key in self._param_order] for params in combos]
        return np.array(arr)

    def _np_to_params(self,arr):
        return {key: val for key, val in zip(self._param_order, arr)}

