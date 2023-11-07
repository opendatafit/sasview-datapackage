def main(datapackage, params, options, data, outputs, **kwargs):
    import sasmodels as sm
    import sasmodels.data
    import sasmodels.core
    import sasmodels.bumps_model

    from sas.sascalc.dataloader.loader import Loader

    import bumps.fitproblem
    import bumps.fitters
    from bumps.options import FIT_CONFIG

    import numpy as np
    import pandas as pd

    import base64
    import io
    import tempfile
    from copy import deepcopy

    import opendatafit

    # =========================================================================
    # SasView helpers

    def parameter_resource_to_bumps_model(kernel, resource):
        # TODO: Need a way to include/exclude PD parameters depending on option
        # selection...
        params = {
            key: param["value"] for key, param in resource["data"].items()
        }
        model = sm.bumps_model.Model(kernel, **params)

        for key, param in resource["data"].items():
            if type((getattr(model, key))) == bumps.parameter.Parameter:
                vary = param.get("vary", None)
                if vary is not None:
                    if vary:
                        # (this also sets fixed=False in bumps)
                        getattr(model, key).limits = (
                            param["lowerBound"],
                            param["upperBound"],
                        )
                        getattr(model, key).fixed = False
                    else:
                        getattr(model, key).fixed = True

        return model

    def bumps_model_to_parameter_resource(model, params, stderr):
        """Updates params resource with fitted parameters from bumps model"""
        # bumps_model.Model class is a wrapper around sasview_model.Model,
        # containing fitting information on top of model definition

        # Build dict of fitted params vs. stderr
        # Get array of only fitted parameters
        fit_params = []
        for p in model.parameters().values():
            if not p.fixed:
                fit_params.append(p.name)
        # Build stderr dict
        if stderr is not None:
            stderr_dict = dict(zip(fit_params, stderr[0]))

        # TODO: This actually sets SF parameters as well... oops
        for param_name in params["data"].keys():
            # Set parameter value
            params["data"][param_name]["value"] = model.state()[param_name]

            # If parameter is fitted, add stderr
            if params["data"][param_name].get("vary") is not None:
                # TODO: check "vary" exists, and not PD parameter??

                # Get parameter stderr
                stderr_value = None
                if stderr is not None:
                    stderr_value = stderr_dict.get(param_name)

                # Add error field
                param_schema = opendatafit.datapackage.find(
                    params["schema"]["keys"], "name", param_name
                )
                param_schema["fields"].append(
                    {
                        "name": "stderr",
                        "title": "Error",
                        "type": "number",
                    }
                )

                # Set error
                params["data"][param_name]["stderr"] = stderr_value

        return params

    def sasview_data_1d_to_resource(data):
        """Convert SASView Data1D object to a Frictionless Data Resource"""
        return opendatafit.datapackage.dataframe_to_tabular_data_resource(
            df=pd.DataFrame(
                data={
                    "x": data.x,
                    "y": data.y,
                    "dx": data.dx,
                    "dy": data.dy,
                    "lam": data.lam,
                    "dlam": data.dlam,
                }
            ),
            resource={
                "schema": {
                    "primaryKey": "x",
                    "fields": [
                        {
                            "name": "x",
                            "type": "number",
                            "title": data._xaxis,
                            "unit": data._xunit,
                        },
                        {
                            "name": "y",
                            "type": "number",
                            "title": data._yaxis,
                            "unit": data._yunit,
                        },
                        {"name": "dx", "type": "number"},
                        {"name": "dy", "type": "number"},
                        {"name": "lam", "type": "number"},
                        {"name": "dlam", "type": "number"},
                    ],
                },
            },
        )

    # =========================================================================
    # Analysis algorithm

    # Get algorithm inputs
    data = opendatafit.datapackage.get_algorithm_io_resource(
        datapackage=datapackage,
        algorithm_name="sasview",
        io_type="input",
        io_name="data",
    )

    param_space = opendatafit.datapackage.get_algorithm_io_resource(
        datapackage=datapackage,
        algorithm_name="sasview",
        io_type="input",
        io_name="params",
    )

    # Write to temporary file for reading by SASView
    # TODO: Would be ideal not to have to do this...
    with tempfile.NamedTemporaryFile(
        dir="/tmp",
        prefix=data["title"].rsplit(".", 1)[0] + "_",
        suffix="." + data["title"].rsplit(".", 1)[1],
    ) as f:
        # Create temporary file to be loaded by sasview
        # Need to spoof in-memory data as a file BytesIO object
        f.write(io.BytesIO(base64.b64decode(data["data"])).getbuffer())

        loader = Loader()

        # TODO: Handle multiple return Data objs - example file for this case?
        # TODO: Handle Data2D case
        data_sas = loader.load(f.name)
        data_sas = data_sas[0]

    # TODO: Setting beamstop breaks dataset construction as X doesn't match
    # fitted data - fix this via to_masked_array maybe??
    # sm.data.set_beam_stop(data_sas, 0.008, 0.19)

    # Get selected model name
    model_name = param_space["params"]["metadata"]["model"]["name"]
    sf_name = None

    try:
        # sfParams exists
        sf_name = param_space["sfParams"]["metadata"]["model"]["name"]
        load_name = model_name + "@" + sf_name
    except TypeError:
        # sfParams is null
        load_name = model_name

    kernel = sm.core.load_model(load_name)

    # Initialise model
    # TODO: Handle error if no params are set to "vary"
    # Bumps fails non-gracefully on this

    # Get parameter resources
    params = param_space["params"]
    sf_params = param_space["sfParams"]

    if sf_name and sf_params is not None:
        # Combine form factor/polydispersity params with structure factor
        # params in single resource for passing to sasview
        all_params = deepcopy(params)
        all_params["data"].update(sf_params["data"])
        all_params["schema"]["keys"].append(sf_params["schema"]["keys"])

    if kwargs.get("plot_only", False):
        # Don't fit if plot_only flag set
        run_fit = False
    else:
        # TODO
        # TEMP: Set vary to true to run fit for testing
        params["data"]["radius"]["vary"] = True

        # Are there any parameters to be fitted?
        # (checks if any parameters are set to "vary")
        run_fit = np.any(
            [value.get("vary", False) for key, value in params["data"].items()]
        )

    # Create bumps model
    model = parameter_resource_to_bumps_model(kernel, params)

    # Set up bumps fitter
    M = sm.bumps_model.Experiment(data=data_sas, model=model)
    problem = bumps.fitproblem.FitProblem(M)

    # Set bumps to use selected fitter method
    fitter_name = param_space["options"]["data"]["method"]["name"]
    FIT_CONFIG.selected_id = getattr(bumps.fitters, fitter_name).id

    fitter = FIT_CONFIG.selected_fitter
    fitter_options = FIT_CONFIG.selected_values

    # Set up fitter and run
    fitdriver = bumps.fitters.FitDriver(
        fitter, problem=problem, **fitter_options
    )

    if run_fit:
        best, fbest = fitdriver.fit()

    # Populate datapackage outputs
    output_data = opendatafit.datapackage.get_algorithm_io_resource(
        datapackage, "sasview", "output", "data"
    )
    output_params = opendatafit.datapackage.get_algorithm_io_resource(
        datapackage, "sasview", "output", "params"
    )
    output_sf_params = opendatafit.datapackage.get_algorithm_io_resource(
        datapackage, "sasview", "output", "sfParams"
    )
    output_fit = opendatafit.datapackage.get_algorithm_io_resource(
        datapackage, "sasview", "output", "fit"
    )
    output_fit_stats = opendatafit.datapackage.get_algorithm_io_resource(
        datapackage, "sasview", "output", "fitStats"
    )

    # Populate output data table
    output_data.update(sasview_data_1d_to_resource(data_sas))

    # Populate output fit table
    # TODO: CHECK CONVERGENCE
    # (test by setting initial params to something bad - theory() returns NaNs)
    output_fit.update(
        opendatafit.datapackage.dataframe_to_tabular_data_resource(
            df=pd.DataFrame(
                data={
                    "x": problem.fitness._data.x.flatten(),
                    "y": problem.fitness.theory().flatten(),
                    "y_raw": problem.fitness._data.y.flatten(),
                    "residuals": problem.fitness.residuals().flatten(),
                }
            ),
            resource={
                "schema": {
                    "primaryKey": "x",
                    "fields": [
                        {
                            "name": "x",
                            "type": "number",
                            "title": data_sas._xaxis,
                            "unit": data_sas._xunit,
                        },
                        {
                            "name": "y",
                            "type": "number",
                            "title": data_sas._yaxis,
                            "unit": data_sas._yunit,
                        },
                        {
                            "name": "y_raw",
                            "type": "number",
                            "title": data_sas._yaxis,
                            "unit": data_sas._yunit,
                        },
                        {
                            "name": "residuals",
                            "type": "number",
                            "title": data_sas._yaxis,
                            "unit": data_sas._yunit,
                        },
                    ],
                },
            },
        )
    )

    # Build output parameter resources
    if run_fit:
        stderr = problem.stderr()
    else:
        stderr = None
    output_params.update(
        bumps_model_to_parameter_resource(model, deepcopy(params), stderr)
    )

    if sf_name:
        output_sf_params.update(
            bumps_model_to_parameter_resource(
                model, deepcopy(sf_params), stderr
            )
        )

    # Build output fit statistics resource
    if run_fit:
        chisq = problem.chisq()
        nllf = problem.nllf()
        dof = problem.dof
    else:
        chisq = None
        nllf = None
        dof = None

    # TODO: Add cov - problem.cov()
    # dir(problem.fitness)
    # problem.summarize()
    output_fit_stats.update(
        {
            "data": {
                "chisq": {"value": chisq},
                "nllf": {"value": nllf},
                "dof": {"value": dof},
            }
        }
    )

    return {
        "data": output_data,
        "fit": output_fit,
        "params": output_params,
        "sfParams": output_sf_params,
        "fitStats": output_fit_stats,
    }
