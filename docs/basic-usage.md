# Basic usage

## Upload data file

Click the "Upload new file" box at the top left of the control panel. Use the file browser to find and select the data file you want to fit.

Information on data formats accepted by the SasView backend can be found [here](https://www.sasview.org/docs/user/qtgui/MainWindow/data\_formats\_help.html).

A plot of your input data should appear in the right hand pane if it has been successfully uploaded.

## Select form factor and structure factor models

A list of all available models provided by the SasView backend can be found [here](https://www.sasview.org/docs/user/qtgui/Perspectives/Fitting/models/index.html).

Structure factor model selection is only enabled for some form factor models.

## Select parameters to be fitted and set initial values

By default, no parameters are selected to be included in the fit. If the fit is run in this state, it will return a fit curve calculated directly from the initial parameter value guesses without optimising any values. This can be useful to get an idea of how close your initial parameter guesses are to the observational data. You can also use the "Plot" button at the bottom left of the control panel to render this plot at any time.

Once you are happy with your initial parameter value guesses, select the parameters you would like to optimise by ticking the relevant checkboxes under the "Fit" column in the control panel.

## Set fit options

### Fit method

This sets the fit optimisation algorithm to be used. Further information on the available optimisers can be found in the SasView documentation [here](https://www.sasview.org/docs/user/qtgui/Perspectives/Fitting/optimizer.html).

## Run

Once happy with your chosen settings, you can run the fit optimisation by clicking "Run" at the bottom right of the control panel.
