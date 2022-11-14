# DIPLOMAT

Deep learning-based Identity Preserving Labeled-Object Multi-Animal Tracking.

**NOTE:** DIPLOMAT is currently alpha software, there may be minor bugs and issues.

## About

DIPLOMAT provides algorithms and tools for performing multi-animal identity preserving tracking on top of single animal and multi animal CNN based tracking packages. Currently, it supports running on both single and multi animal DeepLabCut projects, but can be extended to support other tracking
packages. Unlike other multi-animal tracking packages, DIPLOMAT's algorithms work directly off confidence maps instead of running peak detection, allowing for more nuanced tracking results compared to other methods. 

|                                                            |                                                  |
|------------------------------------------------------------|--------------------------------------------------|
| ![Example of tracking 2 Degus in a Box](docs/source/_static/imgs/example1.png) | ![Example of tracking 3 Rats](docs/source/_static/imgs/example2.png) |

DIPLOMAT also includes a UI for performing tracking and several other tools for storing and visualizing confidence maps. 

![UI Demo Showing user correcting tracking in a video](docs/source/_static/imgs/UIDemo.png)

## Installation

To install DIPLOMAT with PIP right now, you can and install it with pip using the following command:
```bash
pip install git+https://github.com/TravisWheelerLab/DIPLOMAT.git
```
To install DIPLOMAT with GUI elements and supervised tracking support, use the command below:
```bash
pip install "diplomat-track[gui] @ git+https://github.com/TravisWheelerLab/DIPLOMAT.git"
```

**NOTE:** DIPLOMAT also includes an environment configuration file for setting up DIPLOMAT with conda.
To create an environment using conda, and activate it, run these commands:
```bash
# Create the environment...
conda env create -f https://raw.githubusercontent.com/TravisWheelerLab/DIPLOMAT/main/conda-environments/DIPLOMAT-DEEPLABCUT.yaml
# Activate it...
conda activate DIPLOMAT-DEEPLABCUT
```

## Usage

#### Running DIPLOMAT

To run DIPLOMAT on a video once it is installed, simply use DIPLOMAT's `unsupervised` and `supervised` commands to track a video:
```bash
# Run DIPLOMAT with no UI...
diplomat unsupervised -c path/to/config -v path/to/video
# Run DIPLOMAT with UI...
diplomat supervised -c path/to/config -v path/to/video
```

Multiple videos can be tracked by passing them as a list:
```bash
diplomat unsupervised -c path/to/config -v [path/to/video1, path/to/video2, "path/to/video3"]
```

Once tracking is done, DIPLOMAT can create labeled videos via it's `annotate` subcommand:
```bash
diplomat annotate -c path/to/config -v path/to/video
```

If you need to make minor modifications after tracking a video, you can do so using the tweak subcommand:
```bash
diplomat tweak -c path/to/config -v path/to/video
```
This will display a stripped down version of the supervised editing UI, allowing for minor tweaks to be made to the tracks, and then
saved back to the same file.

To see 

#### Additional Help

All diplomat commands are documented via help strings. To get more information about a diplomat subcommand or command, simply run it with the `-h` or `--help` flag.

```bash
# Help for all of diplomat (lists sub commands of diplomat):
diplomat --help 
# Help for the track subcommand:
diplomat track --help
# Help for the predictors subcommand space:
diplomat predictors --help
```

## Development

DIPLOMAT is written entirely in python. To set up an environment for developing DIPLOMAT, you can simply pull down this repository and install its
requirements using poetry. For a further description of how to set up DIPLOMAT for development, see the ...

## Contributing

We welcome external contributions, although it is a good idea to contact the
maintainers before embarking on any significant development work to make sure
the proposed changes are a good fit.

Contributors agree to license their code under the license in use by this
project (see `LICENSE`).

To contribute:

  1. Fork the repo
  2. Make changes on a branch
  3. Create a pull request

## License

See `LICENSE` for details.

## Authors

If you have any questions, feel free to reach out to Isaac Robinson, at [isaac.k.robinson2000@gmail.com](mailto:isaac.k.robinson2000@gmail.com)

See `AUTHORS` the full list of authors.

