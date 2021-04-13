[![license](https://img.shields.io/github/license/pcjco/batch_on_files)](https://github.com/pcjco/batch_on_files/blob/master/LICENSE)

![Screenshot](screenshot.png?raw=true "Screenshot")

This application uses a list of input files and will process a script on each one using parallel processes.

### Command line options

<pre>
usage: batch_on_files.py [-h] [--cpu CPU] --script SCRIPT files [files ...]

Loop a CMD script on files.

positional arguments:
  files            files to process

optional arguments:
  -h, --help       show this help message and exit
  --cpu CPU        number of process (default = number of CPU)
  --script SCRIPT  batch to launch on every other files
</pre>