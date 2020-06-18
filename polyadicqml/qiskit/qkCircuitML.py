"""Implementeation of quantum circuit for ML using qiskit API.
"""
import qiskit as qk
from qiskit.providers.aer.noise import NoiseModel
from qiskit.exceptions import QiskitError
from qiskit.providers import JobStatus

from sys import exc_info
from os.path import isfile
import numpy as np

from time import asctime, sleep
from itertools import cycle
import json

from .utility.backends import Backends
from ..circuitML import circuitML
from .qiskitBdr import ibmqNativeBuilder

class qkCircuitML(circuitML):
    """Quantum ML circuit interface for qiskit and IBMQ.
    Provides a unified interface to run multiple parametric circuits with different input and model parameters. 
    """
    def __init__(self, backend, make_circuit, nbqbits, nbparams,
                 cbuilder=ibmqNativeBuilder, 
                 noise_model=None, noise_backend=None,
                 save_path=None):
        """Create qkCircuitML cricuit.

        Parameters
        ----------
        backend : Union[Backends, list, qiskit.providers]
            Backend on which to run the circuits
        circuitBuilder : circuitBuilder
            Circuit builder.
        nbqbits : int
            Number of qubits.
        noise_model : Union[list, qiskit.providers.aer.noise.NoiseModel], optional
            Noise model to be provided to the backend, by default None. Cannot be used with `noise_backend`.
        noise_backend : Union[Backends, list, qiskit.IBMQBackend], optional
            IBMQ backend from which the noise model should be generated, by default None.
        save_path : str, optional
            Where to save the jobs outputs, by default None. Jobs are saved only if a path is specified
, 
        Raises
        ------
        ValueError
            If both `noise_model` and `noise_backend` are provided.
        """
        super().__init__(make_circuit, nbqbits, nbparams, cbuilder)

        self.save_path = save_path

        if isinstance(backend, Backends):
            self.__backend__ = backend
            self.backend = self.__backend__.backends
            self.noise_model = self.__backend__.noise_models
            self.coupling_map = self.__backend__.coupling_maps

        else:
            self.backend = cycle(backend) if isinstance(backend, list) else cycle([backend])

            if noise_model is not None and noise_backend is not None:
                raise ValueError("Only one between 'noise_model' and 'noise_backend' can be passed to the constructor")

            self.noise_model = cycle(noise_model) if isinstance(noise_model, list) else cycle([noise_model])
            self.coupling_map = cycle([None])

            if noise_backend is not None:
                _noise_back = noise_backend if isinstance(noise_backend, list) else [noise_backend]

                self.noise_model = cycle([NoiseModel.from_backend(_backend) for _backend in _noise_back])
                self.coupling_map = cycle([_backend.configuration().coupling_map for _backend in _noise_back])

    def run(self, X, params, shots=None, job_size=None):
        try:
            if not job_size:
                job, qc_list = self.request(X, params, shots)
                try:
                    return self.result(job, qc_list, shots)
                except:
                    status = job.status()
                    if job.done() or status == JobStatus.DONE:
                        print(f"Completed job {job.job_id()} on {job.backend().name()}")
                    elif status in (JobStatus.CANCELLED, JobStatus.ERROR):
                        print(f"{status} ({job.job_id()}) on {job.backend().name()}")
                    else:
                        print(f"Cancelling job {job.job_id()} on {job.backend().name()}")
                        job.cancel()
                    raise
            else:
                if not isinstance(job_size, int): raise TypeError("'job_size' has to be int")

                n_jobs = len(X) // job_size
                requests = [self.request(X[job_size * n : job_size * (n+1)], params, shots) for n in range(n_jobs)] 
                if job_size * n_jobs < len(X):
                    requests.append(self.request(X[job_size * n_jobs :], params, shots))
                try:
                    return np.vstack([self.result(job, qc_list, shots) for job, qc_list in requests])
                except:
                    for job, qc_list in requests:
                        status = job.status()
                        if job.done() or status == JobStatus.DONE:
                            print(f"Completed job {job.job_id()} on {job.backend().name()}")
                        elif status in (JobStatus.CANCELLED, JobStatus.ERROR):
                            print(f"{status} ({job.job_id()}) on {job.backend().name()}")
                        else:
                            print(f"Cancelling job {job.job_id()} on {job.backend().name()}")
                            job.cancel()
                    raise
        except KeyboardInterrupt:
            cin = input("[r] to reload backends, [ctrl-c] to confirm interrupt :\n")
            if cin == 'r':
                self.__backend__.load_beckends()
                self.backend = self.__backend__.backends
                self.noise_model = self.__backend__.noise_models
                self.coupling_map = self.__backend__.coupling_maps

            return self.run(X, params, shots, job_size)
        except QiskitError:
            print(f"{asctime()} - Error in qkCircuitML.run :{exc_info()[0]}", end="\n\n")
            with open("error.log", "w") as f:
                f.write(f"{asctime()} - Error in qkCircuitML.run :{exc_info()[0]}\n")
            sleep(10)
            return self.run(X, params, shots, job_size)

    def make_circuit_list(self, X, params, shots=None):
        """Generate a circuit for each sample in `X` rows, with parameters `params`.

        Parameters
        ----------
        X : array-like
            Input matrix, of shape (nb_samples, nb_features) or (nb_features,). In the latter case, nb_samples is 1.
        params : vector-like
            Parameter vector.
        shots : int, optional
            Number of shots, by default None

        Returns
        -------
        list[qiskit.QuantumCircuit]
            List of nb_samples circuits.
        """
        if len(X.shape) < 2:
            return [self.make_circuit(self, X, params, shots)]
        else:
            return [self.make_circuit(self, x, params, shots) for x in X]

    def request(self, X, params, shots=None):
        """Create circuits corresponding to samples in `X` and parameters `params` and send jobs to the backend for execution.

        Parameters
        ----------
        X : array-like
            Input matrix, of shape (nb_samples, nb_features) or (nb_features,). In the latter case, nb_samples is 1.
        params : vector-like
            Parameter vector.
        shots : int, optional
            Number of shots, by default None

        Returns
        -------
        (qiskit.providers.BaseJob, list[qiskit.QuantumCircuit])
            Job instance derived from BaseJob and list of corresponding circuits.
        """
        qc_list = self.make_circuit_list(X, params, shots)

        # Optional arguments for execute are defined here, if they have been given at construction.
        execute_kwargs = {}
        if shots:
            execute_kwargs['shots'] = shots

        _noise_model = next(self.noise_model)
        if _noise_model is not None:
            execute_kwargs['basis_gates'] = _noise_model.basis_gates
            execute_kwargs['noise_model'] = _noise_model
        _coupling_map = next(self.coupling_map)
        if _coupling_map is not None:
            execute_kwargs['coupling_map'] = _coupling_map

        return qk.execute(qc_list, next(self.backend),
                  **execute_kwargs,
                 ), qc_list


    def result(self, job, qc_list, shots=None):
        """Retrieve job results and returns bitstring counts.

        Parameters
        ----------
        job : qiskit.providers.BaseJob
            Job instance.
        qc_list : list[qiskit.QuantumCircuit]
            List of quantum circuits executed in `job`, of length nb_samples.
        shots : int, optional
            Number of shots, by default None. If None, raw counts are returned.

        Returns
        -------
        array
            Bitstring counts as an array of shape (nb_samples, 2**nbqbits), in the same order as `qc_list`.

        Raises
        ------
        QiskitError
            If job status is cancelled or had an error.
        """
        wait = 1
        while not job.done():
            if job.status() in (JobStatus.CANCELLED, JobStatus.ERROR): raise QiskitError
            sleep(wait)
            #if wait < 20 : wait *= 5

        results = job.result()
        if not shots:
            out = [results.get_statevector(qc) for qc in qc_list]
            out = np.abs(out)**2
            order = [int(f"{key:0>{self.nbqbits}b}"[::-1], 2)
                        for key in range(out.shape[1])]
            return out[:, order]
        else:
            out = np.zeros((len(qc_list), 2**self.nbqbits))
            for n, qc in enumerate(qc_list):
                for key, count in results.get_counts(qc).items():
                    # print(f"{key} : {count}")
                    out[n, int(key[::-1], 2)] = count

        if self.save_path: self.save_job(job)
        return out

    def save_job(self, job, save_path=None):
        """Save job output to json file.

        Parameters
        ----------
        job : qiskit.providers.BaseJob
            Job instance.
        save_path : path, optional
            Where to save the output, by default None. If None, uses `self.save_path`.
        """
        save_path = self.save_path if save_path is None else save_path

        if isfile(save_path):
            try:
                with open(save_path) as f:
                    out = json.load(f)
            except:
                print(f"ATTENTION: file {save_path} is broken, confirm overwriting!")
                input("Keybord interrupt ([ctrl-c]) to abort")
                out = {}
        else:
            out = {}

        with open(save_path, 'w') as f:
            job_id = job.job_id()
            try:
                times = job.time_per_step()
                info = {key : str(times[key]) for key in times}
            except AttributeError:
                info = {}
            info['results'] = job.result().to_dict()

            out[job_id] = info
            json.dump(out, f)