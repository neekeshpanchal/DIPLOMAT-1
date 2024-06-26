Installation
============

DIPLOMAT currently supports being installed as a normal python package on Windows, Linux, and MacOS.
DIPLOMAT and can be installed by following the installation guide below.

.. contents:: Contents



Installing Python
-----------------

If you have not already, you'll need to install python to utilize DIPLOMAT. It is recommend that you use
`Miniforge <https://github.com/conda-forge/miniforge>`_ which provides a python environment
and install process that is consistent across platforms. To install Miniforge:

 - Visit `https://github.com/conda-forge/miniforge <https://github.com/conda-forge/miniforge>`_.
 - Select the installer for your OS from the list of installers.
 - Run the installer and follow the installation instructions.

Installing DIPLOMAT
-------------------

With Support for DeepLabCut Projects
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Using Mamba or Conda
~~~~~~~~~~~~~~~~~~~~

Once you have mamba or a mamba compatible CLI installed, you'll want to open a terminal and type one of these
two commands:

.. code-block:: sh

    # Install diplomat with GPU support...
    mamba env create -f https://raw.githubusercontent.com/TravisWheelerLab/DIPLOMAT/main/conda-environments/DIPLOMAT-DEEPLABCUT.yaml
    # Install diplomat with CPU support only...
    mamba env create -f https://raw.githubusercontent.com/TravisWheelerLab/DIPLOMAT/main/conda-environments/DIPLOMAT-DEEPLABCUT-CPU.yaml

.. hint::

    Both running and installing diplomat requires access to a terminal. To access one:

    **Windows:** Open the start menu and search for *Miniforge Prompt*.

    **Linux:** Press :kbd:`CTRL` + :kbd:`ALT` + :kbd:`T`. This will open a terminal window.

    **Mac:** Select the search icon in the top right corner of the screen to open Spotlight, and
    then search for *Terminal*.

Once done, simply activate the brand new environment.

.. code-block:: sh

    mamba activate DIPLOMAT-DEEPLABCUT

From here, the ``diplomat`` command will be available from the command line.

Using PIP
~~~~~~~~~

If you are using an alternative package for managing python environments, you can install
DIPLOMAT with DeepLabCut support by simply using pip, using one of the two commands below:

.. code-block:: sh

    # Install DIPLOMAT with DeepLabCut with GUI support.
    pip install diplomat-track[dlc, gui]
    # Install DIPLOMAT with DeepLabCut without UI support.
    pip install diplomat-track[dlc]


With Support for SLEAP Projects
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Using Mamba or Conda
~~~~~~~~~~~~~~~~~~~~

Once you have a mamba installed, you'll want to open a terminal and type one of these two commands:

.. code-block:: sh

    # Install diplomat with GPU support...
    mamba env create -f https://raw.githubusercontent.com/TravisWheelerLab/DIPLOMAT/main/conda-environments/DIPLOMAT-SLEAP.yaml
    # Install diplomat with CPU support only...
    mamba env create -f https://raw.githubusercontent.com/TravisWheelerLab/DIPLOMAT/main/conda-environments/DIPLOMAT-SLEAP-CPU.yaml

.. hint::

    Both running and installing diplomat requires access to a terminal. To access one:

    **Windows:** Open the start menu and search for *Miniforge Prompt*.

    **Linux:** Press :kbd:`CTRL` + :kbd:`ALT` + :kbd:`T`. This will open a terminal window.

    **Mac:** Select the search icon in the top right corner of the screen to open Spotlight, and
    then search for *Terminal*.

Once done, simply activate the brand new environment.

.. code-block:: sh

    mamba activate DIPLOMAT-SLEAP

From here, the ``diplomat`` command will be available from the command line.

Using PIP
~~~~~~~~~

If you are using an alternative package for managing python environments, you can install
DIPLOMAT with SLEAP support by simply using pip, using one of the two commands below:

NOTE: SLEAP is known to have installation issues on Windows when attempting to use pip. If you're
trying to install DIPLOMAT with SLEAP support on Windows, prefer using the mamba/miniforge method above.

.. code-block:: sh

    # Install DIPLOMAT with SLEAP with GUI support.
    pip install diplomat-track[sleap, gui]
    # Install DIPLOMAT with SLEAP without UI support.
    pip install diplomat-track[sleap]