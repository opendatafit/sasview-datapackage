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

    from frictionless import Resource

    import base64
    import io

    import tempfile

    # =========================================================================
    # General helpers

    def find(lst, key, value):
        # Return item item matching i[key] == value in a list of dicts
        for dic in lst:
            if dic[key] == value:
                return dic
        return None

    # =========================================================================
    # SAS helpers

    def parameter_resource_to_bumps_model(kernel, resource):
        # TODO: Need a way to include/exclude PD parameters depending on option
        # selection...

        params = resource.get_values()
        model = sm.bumps_model.Model(kernel, **params)

        for key, param in resource.data.items():
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
        """
        " params: frictionless.Resource
        " model: sasmodels.bumps_model.Model
        "
        " Updates params resource with fitted parameters from bumps model
        """

        # bumps_model.Model class is a wrapper around sasview_model.Model,
        # containing fitting information on top of model definition

        # Get array of only fitted parameters
        fitted_params = []
        for p in model.parameters().values():
            if not p.fixed:
                fitted_params.append(p.name)

        # Create dict of fitted parameters vs. stderr values
        if stderr is not None:
            stderr_dict = dict(zip(fitted_params, stderr[0]))
        else:
            stderr_dict = None

        print("=========================================")
        # TODO: This actually sets SF parameters as well... oops
        for param in params.get_values().keys():
            stderr_value = (
                stderr_dict.get(param) if stderr_dict is not None else None
            )

            params.set_param(
                key=param,
                value=model.state()[param],
                field="value",
            )

            # If parameter is fitted...
            if params.get_param(key=param, field="vary"):
                # Get parameter stderr
                print(param, "error:", stderr_value)

                # Add error field
                # TODO: check "vary" exists, and not PD parameter??
                params.add_field(
                    key=param,
                    name="stderr",
                    title="Error",
                    type="number",
                )

                # Set error
                params.set_param(
                    key=param,
                    value=stderr_value,
                    field="stderr",
                )

        print("=========================================")
        return params

    def data_1d_to_resource(data):
        """
        " Convert SASView Data1D object to a Frictionless Data Resource
        """

        df = pd.DataFrame(
            data={
                "x": data.x,
                "y": data.y,
                "dx": data.dx,
                "dy": data.dy,
                "lam": data.lam,
                "dlam": data.dlam,
            }
        )

        return Resource(
            df,
            name="data",
            format="pandas",
            schema={
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
        )

    # =========================================================================
    # Extended Resource classes

    class ParameterResource(Resource):
        def concat(self, resource):
            # Check profiles match
            assert self.profile == resource.profile

            # Concat schema
            # TODO: Match any identical descriptors in schema?
            # TODO: Implement custom ParameterSchema object
            keys = self.schema.to_dict()["keys"]
            keys.append(resource.schema.to_dict()["keys"])
            self.schema["keys"] = keys

            # Concat data
            self.data.update(resource.data)

        def get_param(self, key, field=None):
            try:
                if field is not None:
                    return self.data[key][field]
                else:
                    return self.data[key]
            except KeyError:
                return None

        def set_param(self, key, value, field=None):
            if field is not None:
                self.data[key][field] = value
            else:
                self.data[key] = value

        def get_values(self, field="value"):
            # TODO: Handle non-float values!

            def is_float(value):
                try:
                    float(value)
                    return True
                except:  # noqa: E722
                    return False

            return {
                key: float(row.get(field))
                if is_float(row.get(field))
                else row.get(field)
                for key, row in self.data.items()
            }

        def add_field(self, key, name, **kwargs):
            # TODO: Test this works
            try:
                key_schema = find(self.schema["keys"], "name", key)
            except TypeError:
                # Ignore error caused by trying to add a field to a
                # non-existent key... from add_field
                return
            key_schema["fields"].append({"name": name, **kwargs})

    class OptionsResource(Resource):
        def get_option(self, option):
            return self.data[option]["name"]

        def set_option(self, option, key):
            self.data[option] = self.get_option_choice(option, key)

        def get_option_choice(self, option, key):
            field = find(self.schema.fields, "name", option)
            return find(field["constraints"]["enum"], "name", key)

    class FileResource(Resource):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            if not self.data:
                raise RuntimeError("FileResource file data cannot be empty")

        @property
        def file(self):
            return io.BytesIO(base64.b64decode(self.data))

        @property
        def file_name(self):
            try:
                return self.title.rsplit(".", 1)[0]
            except IndexError:
                return ""

        @property
        def file_ext(self):
            try:
                return self.title.rsplit(".", 1)[1]
            except IndexError:
                return ""

    # =========================================================================
    # Analysis algorithm

    data_resource = FileResource(descriptor=data)

    # Write to temporary file for reading by SASView
    # TODO: Would be ideal not to have to do this...
    with tempfile.NamedTemporaryFile(
        dir="/tmp",
        prefix=data_resource.file_name + "_",
        suffix="." + data_resource.file_ext,
    ) as f:
        # Create temporary file to be loaded by sasview
        f.write(data_resource.file.getbuffer())

        loader = Loader()

        # TODO: Handle multiple return Data objs - example file for this case?
        # TODO: Handle Data2D case
        data_sas = loader.load(f.name)
        data_sas = data_sas[0]

    # TODO: Setting beamstop breaks dataset construction as X doesn't match
    # fitted data - fix this via to_masked_array maybe??
    # sm.data.set_beam_stop(data_sas, 0.008, 0.19)

    # TODO: TEMP
    # Convert options JSON into Resource
    options_resource = OptionsResource(descriptor=options)

    # Get selected model name
    model_name = options_resource.get_option("model")
    sf_name = options_resource.get_option("structureFactor")

    if sf_name:
        load_name = model_name + "@" + sf_name
    else:
        load_name = model_name

    kernel = sm.core.load_model(load_name)

    # Initialise model
    # TODO: Error handling for if no params are set to "vary" - bumps fails
    # non gracefully on this

    # TODO: TEMP
    # Convert params JSON into Resource
    params_resource = ParameterResource(descriptor=params)
    # TODO: TEMP gross, used below to convert bumps results to resources
    model_params = params_resource
    # TODO: Get sf_params here
    sf_params = None
    if sf_name and sf_params is not None:
        sf_params_resource = ParameterResource(descriptor=sf_params)
        # TODO: TEMP gross, used below to convert bumps results to resources
        sf_model_params = sf_params_resource

        # Combine form factor/polydispersity params with SF params
        params_resource.concat(sf_params_resource)

    if kwargs.get("plot_only", False):
        # Don't fit if plot_only flag set
        run_fit = False
    else:
        # Are there any parameters to be fitted?
        # (checks if any parameters are set to "vary")
        run_fit = np.any(
            list(params_resource.get_values(field="vary").values())
        )

    # Create bumps model
    model = parameter_resource_to_bumps_model(kernel, params_resource)

    # Set up bumps fitter
    M = sm.bumps_model.Experiment(data=data_sas, model=model)
    problem = bumps.fitproblem.FitProblem(M)

    # Set bumps to use selected fitter method
    fitter_name = options_resource.get_option("method")
    FIT_CONFIG.selected_id = getattr(bumps.fitters, fitter_name).id

    fitter = FIT_CONFIG.selected_fitter
    fitter_options = FIT_CONFIG.selected_values

    # Set up fitter and run
    fitdriver = bumps.fitters.FitDriver(
        fitter, problem=problem, **fitter_options
    )

    if run_fit:
        best, fbest = fitdriver.fit()
        # print("====================================================")
        # import pprint
        # pprint.pprint(dir(problem.fitness))
        # pprint.pprint(dir(problem))
        # print("chisq", problem.chisq())
        # print("nllf", problem.nllf())
        # print("cov", problem.cov())
        # print("dof", problem.dof)
        # Returns array of stderr corresponding with all fitted params
        # + something else... see docs
        # print("stderr", problem.stderr())
        # print("summarize", problem.summarize())
        # print("====================================================")

    # Build fit curve resource
    # TODO: CHECK CONVERGENCE
    # (test by setting initial params to something bad - theory() returns NaNs)
    fit_df = pd.DataFrame(
        data={
            "x": problem.fitness._data.x.flatten(),
            "y": problem.fitness.theory().flatten(),
            "y_raw": problem.fitness._data.y.flatten(),
            "residuals": problem.fitness.residuals().flatten(),
        }
    )

    fit_table = Resource(
        fit_df,
        name="fit",
        format="pandas",
        schema={
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
    )

    # TODO: Move this to a library function OR find a way to use
    # PandasJSONEncoder from datafit-framework:datatypes/package.js inside
    # container-base before data is returned...

    # Convert data from Pandas DataFrame to JSON rows for serialization
    rows = fit_table.data.to_dict("records")
    fit_table.data = rows

    data_sas_resource = data_1d_to_resource(data_sas)
    rows = data_sas_resource.data.to_dict("records")
    data_sas_resource.data = rows

    # Optimised parameter resources
    if run_fit:
        stderr = problem.stderr()
    else:
        stderr = None
    fit_model_params = bumps_model_to_parameter_resource(
        model, model_params, stderr
    )

    if sf_name:
        fit_model_sf_params = bumps_model_to_parameter_resource(
            model, sf_model_params, stderr
        )
    else:
        fit_model_sf_params = {}

    if run_fit:
        chisq = problem.chisq()
        nllf = problem.nllf()
        dof = problem.dof
    else:
        chisq = None
        nllf = None
        dof = None

    fit_statistics = {
        "name": "sas_result_fit_statistics",
        "title": "Fit statistics",
        "description": "Goodness of fit statistics",
        "profile": "data-resource",
        "tmpprofile": "parameter-data-resource",
        "data": {
            "chisq": {"value": chisq},
            "nllf": {"value": nllf},
            "dof": {"value": dof},
        },
        "format": "json",
        "schema": {
            "fields": [],
            "keys": [
                {
                    "fields": [
                        {"name": "value", "title": "Value", "type": "number"}
                    ],
                    "name": "chisq",
                    "title": "Chi Squared",
                    "unit": "",
                },
                {
                    "fields": [
                        {"name": "value", "title": "Value", "type": "number"}
                    ],
                    "name": "nllf",
                    "title": "Negative Log Likelihood",
                    "unit": "",
                },
                {
                    "fields": [
                        {"name": "value", "title": "Value", "type": "number"}
                    ],
                    "name": "dof",
                    "title": "Degrees of Freedom",
                    "unit": "",
                },
            ],
        },
    }

    return {
        "result_data": data_sas_resource,
        "result_fit": fit_table,
        "result_params": fit_model_params,
        "result_sf_params": fit_model_sf_params,
        "result_fit_statistics": fit_statistics,
    }
