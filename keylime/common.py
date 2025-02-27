'''
DISTRIBUTION STATEMENT A. Approved for public release: distribution unlimited.

This material is based upon work supported by the Assistant Secretary of Defense for 
Research and Engineering under Air Force Contract No. FA8721-05-C-0002 and/or 
FA8702-15-D-0001. Any opinions, findings, conclusions or recommendations expressed in this
material are those of the author(s) and do not necessarily reflect the views of the 
Assistant Secretary of Defense for Research and Engineering.

Copyright 2015 Massachusetts Institute of Technology.

The software/firmware is provided to you on an As-Is basis

Delivered to the US Government with Unlimited Rights, as defined in DFARS Part 
252.227-7013 or 7014 (Feb 2014). Notwithstanding any copyright notice, U.S. Government 
rights in this work are defined by DFARS 252.227-7013 or DFARS 252.227-7014 as detailed 
above. Use of this work other than as specifically authorized by the U.S. Government may 
violate any copyrights that exist in this work.
'''

import os.path
import ConfigParser
import sys
import urlparse
import json
import tornado.web
from BaseHTTPServer import BaseHTTPRequestHandler
import httplib
import yaml

# Current Keylime API version 
API_VERSION='2'

# SET THIS TO True TO ENABLE THIS TO RUN in ECLIPSE
# in addition to setting the flags:
#     MOUNT_SECURE=False
#     INSECURE_DEBUG=True
#     REQUIRE_ROOT=False
#     STUB_IMA=True
#     DISABLE_EK_CERT_CHECK_EMULATOR=True
# it also does the following:
# default ca_util password is set to 'default' to suppress prompts
# ca_util if passed no args will default to some args listed in ca_util.py
# agent uuid will be set to C432FBB3-D2F1-4A97-9EF7-75BD81C866E9
# data_base files from previous runs will be cleared on startup
# vtpm operations and vtpmmgr commands are all stubbed
# ek certs are not required
# tenant if provided no args will use some args listed in tenant.py
# tenant will also sleep, check status, and then delete the agent after
# a few seconds
DEVELOP_IN_ECLIPSE=False

# SET STUB_TPM TO True TO ALLOW ALL TPM Operations to be stubbed out
# If STUB_TPM=True, TPM_CANNED_VALUES_PATH file must be provided (canned inputs)
# Canned input values can be generated by running with STUB_TPM=False and 
#   specifying a TPM_CANNED_VALUES_PATH filename 
STUB_TPM=False
TPM_CANNED_VALUES_PATH=None

# SET TO TRUE TO STUB A VTPM
STUB_VTPM=False
#force stub tpm if vtpm true
if STUB_VTPM:
    STUB_TPM=True

# Enable TPM benchmarking (output timing data to given file)
TPM_BENCHMARK_PATH=None

# set to False to enable keylime to run from the CWD and not require
# root access.  for testing purposes only
# all processes will log to the CWD in keylime-all.log
REQUIRE_ROOT=True

# enable printing of keys and other info for debug purposes
INSECURE_DEBUG=False

# allow the emuatlor to not have an ekcert even if check ekcert is true
DISABLE_EK_CERT_CHECK_EMULATOR=False

# stub out IMA functionality
STUB_IMA=False

if STUB_TPM:
    STUB_IMA=True
    
# allow testing mode
TEST_MODE = os.getenv('KEYLIME_TEST', 'False')
if TEST_MODE.upper()=='TRUE':
    print "WARNING: running keylime in testing mode.\nkeylime will not run as root and ekcert checking for the TPM emulator is disabled"
    REQUIRE_ROOT=False
    DISABLE_EK_CERT_CHECK_EMULATOR = True

# whether to use tpmfs or not
MOUNT_SECURE=True

# load in JSON canned values if we're in stub mode (and JSON file given)
TPM_CANNED_VALUES=None
if STUB_TPM and TPM_CANNED_VALUES_PATH is not None:
    with open(TPM_CANNED_VALUES_PATH, "rb") as can:
        print "WARNING: using canned values in stub mode from file '%s'"%(TPM_CANNED_VALUES_PATH)
        # Read in JSON and strip trailing extraneous commas 
        jsonInTxt = can.read().rstrip(',\r\n')
        # Saved JSON is missing surrounding braces, so add them here 
        TPM_CANNED_VALUES = json.loads('{' + jsonInTxt + '}')
elif STUB_TPM:
    raise Exception('STUB_TPM=True but required TPM_CANNED_VALUES_PATH not provided!')

# flow the flags down
if DEVELOP_IN_ECLIPSE:
    MOUNT_SECURE=False
    INSECURE_DEBUG=True
    REQUIRE_ROOT=False
    STUB_IMA=True
    DISABLE_EK_CERT_CHECK_EMULATOR=True

if not REQUIRE_ROOT:
    MOUNT_SECURE=False
    
if not REQUIRE_ROOT:
    print "WARNING: running without root access"

# Try and import cLime, if it fails set USE_CLIME to False.
try:
    import _cLime
    USE_CLIME=True
except ImportError:
    USE_CLIME=False

TPM_LIBS_PATH = '/usr/local/lib/'
TPM_TOOLS_PATH = '/usr/local/bin/'
if getattr(sys, 'frozen', False):
    # we are running in a pyinstaller bundle, redirect tpm tools to bundle
    TPM_TOOLS_PATH = sys._MEIPASS

if DEVELOP_IN_ECLIPSE:
    CONFIG_FILE="../keylime.conf"
# elif LOAD_TEST:
#     CONFIG_FILE="./keylime.conf"
else:
    CONFIG_FILE=os.getenv('KEYLIME_CONFIG', '/etc/keylime.conf')

WARN=False
if not os.path.exists(CONFIG_FILE):
    # try to locate the config file next to the script if bundled
    if getattr(sys, 'frozen', False):
        CONFIG_FILE = os.path.dirname(os.path.abspath(sys.executable))+"/keylime.conf"
    else:
        # instead try to get config file from python data_files install
        CONFIG_FILE = os.path.dirname(os.path.abspath(__file__))+"/../package_default/keylime.conf"
        WARN=True
        
if not os.path.exists(CONFIG_FILE):
    raise Exception('%s does not exist. Please set environment variable KEYLIME_CONFIG or see %s for more details'%(CONFIG_FILE, __file__))
print("Using config file %s"%(CONFIG_FILE,))
if WARN:
    print("WARNING: Keylime is using the config file from its installation location. \n\tWe recommend you copy keylime.conf to /etc/ to customize it.")

# read the config file
config = ConfigParser.RawConfigParser()
config.read(CONFIG_FILE)

if not REQUIRE_ROOT:
    WORK_DIR=os.path.abspath(".")
else:
    WORK_DIR=os.getenv('KEYLIME_DIR','/var/lib/keylime')

CA_WORK_DIR='%s/ca/'%WORK_DIR


def chownroot(path,logger):
    if os.geteuid()==0:
        os.chown(path,0,0)
    elif REQUIRE_ROOT:
        logger.debug("Unable to change ownership to root for file: %s"%(path)) 

def ch_dir(path,logger):    
    if not os.path.exists(path):
        os.makedirs(path,0o700)
        chownroot(path,logger)
    os.umask(0o077)
    os.chdir(path)

def echo_json_response(handler,code,status=None,results=None):
    """Takes a JSON package and returns it to the user w/ full HTTP headers"""
    if handler is None or code is None:
        return False
    if status is None:
        status = httplib.responses[code]
    if results is None:
        results = {}
    
    json_res = {'code': code, 'status': status, 'results' : results}
    json_response = json.dumps(json_res)
    
    if isinstance(handler, BaseHTTPRequestHandler):
        handler.send_response(code)
        handler.send_header('Content-Type', 'application/json')
        handler.end_headers()
        handler.wfile.write(json_response)
        return True
    elif isinstance(handler, tornado.web.RequestHandler):
        handler.set_status(code)
        handler.set_header('Content-Type', 'application/json')
        handler.write(json_response)
        handler.finish()
        return True
    else:
        return False

def list_to_dict(list):
    """Convert list into dictionary via grouping [k0,v0,k1,v1,...]"""
    params = {}
    i = 0
    while (i < len(list)):
        params[list[i]] = list[i+1] if (i+1)<len(list) else None
        i = i+2
    return params

def yaml_to_dict(arry):
    return yaml.safe_load("\n".join(arry))

def get_restful_params(urlstring):
    """Returns a dictionary of paired RESTful URI parameters"""
    parsed_path = urlparse.urlsplit(urlstring.strip("/"))
    query_params = urlparse.parse_qsl(parsed_path.query)
    path_tokens = parsed_path.path.split('/')
    
    # If first token is API version, ensure it isn't obsolete
    api_version = API_VERSION
    if len(path_tokens[0]) == 2 and path_tokens[0][0] == 'v':
        # Require latest API version 
        if path_tokens[0][1] != API_VERSION:
            return None
        api_version = path_tokens.pop(0)
    
    path_params = list_to_dict(path_tokens)
    path_params["api_version"] = api_version
    path_params.update(query_params)
    return path_params

# this doesn't currently work
# if LOAD_TEST:
#     config = ConfigParser.RawConfigParser()
#     config.read(CONFIG_FILE)
#     TEST_CREATE_DEEP_QUOTE_DELAY = config.getfloat('general', 'test_deep_quote_delay')
#     TEST_CREATE_QUOTE_DELAY = config.getfloat('general','test_quote_delay')

# NOTE These are still used by platform init in dev in eclipse mode
TEST_PUB_EK='-----BEGIN PUBLIC KEY-----\nMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA1xWZh1aVwKIXT1B9n519\nLE6Oe3QIkeKqUUNURN8wFMd9Acs+vInh5NWgKAHtG4b5KBZqVytvIOJ4NctjinFY\nTCJKM3SJtPA2XYcXaUc6EAQda5TMgfqCeitHjivTtgb3hTMNrIgfOCV40peUU3Im\nSnd84q4Rrq9CfGIdmBCLCzAFfoble6ivMxRVzJ9Ob3xtlaS8ROXKqF+vq0dZZ41Q\nIp6IgpDlSf1TL8w+GHdMQQIUM1XEIRt9Owv8JQvnM4iX06EpnCP/BZshLUN+CivX\n4VRWxjua8NkwMv/wc3xI64E58EYFWnGca3UBi3JBD0QuuzkYM1vkfkvi2QNiWA7I\nFQIDAQAB\n-----END PUBLIC KEY-----\n'
TEST_EK_CERT='MIIDiTCCAnGgAwIBAgIFLgbvovcwDQYJKoZIhvcNAQEFBQAwUjFQMBwGA1UEAxMVTlRDIFRQTSBFSyBSb290IENBIDAyMCUGA1UEChMeTnV2b3RvbiBUZWNobm9sb2d5IENvcnBvcmF0aW9uMAkGA1UEBhMCVFcwHhcNMTMxMDA2MDkxNTM4WhcNMzMxMDA2MDkxNTM4WjAAMIIBXzBKBgkqhkiG9w0BAQcwPaALMAkGBSsOAwIaBQChGDAWBgkqhkiG9w0BAQgwCQYFKw4DAhoFAKIUMBIGCSqGSIb3DQEBCQQFVENQQQADggEPADCCAQoCggEBANcVmYdWlcCiF09QfZ+dfSxOjnt0CJHiqlFDVETfMBTHfQHLPryJ4eTVoCgB7RuG+SgWalcrbyDieDXLY4pxWEwiSjN0ibTwNl2HF2lHOhAEHWuUzIH6gnorR44r07YG94UzDayIHzgleNKXlFNyJkp3fOKuEa6vQnxiHZgQiwswBX6G5XuorzMUVcyfTm98bZWkvETlyqhfr6tHWWeNUCKeiIKQ5Un9Uy/MPhh3TEECFDNVxCEbfTsL/CUL5zOIl9OhKZwj/wWbIS1Dfgor1+FUVsY7mvDZMDL/8HN8SOuBOfBGBVpxnGt1AYtyQQ9ELrs5GDNb5H5L4tkDYlgOyBUCAwEAAaN7MHkwVAYDVR0RAQH/BEowSKRGMEQxQjAUBgVngQUCARMLaWQ6NTc0NTQzMDAwGAYFZ4EFAgITD05QQ1Q0MngvTlBDVDUweDAQBgVngQUCAxMHaWQ6MDM5MTAMBgNVHRMBAf8EAjAAMBMGA1UdJQEB/wQJMAcGBWeBBQgBMA0GCSqGSIb3DQEBBQUAA4IBAQALYCcNLxnWs2rvt/gPGjCfZKuURHmgcICu97IaAM5iJPsyLR14rgOpOXpH1yUbcNvJbljOOfHsCczHOW5rvc+lIOrWHwhPKaRAAVnx7o7Zdj6ndDIqwjMi3royPvM8qad69vVRXTAx/zJkOtWO6eFX0UmPlfpRwVjLbjrbih7rJ58etNH6Umk23iCUriYTXy9HSyuhqQY3f/gxuvQB5v0DIvH6m3ne4mNcvtAv4LMIvKS6PUAjamMHRtebhY3xvGzZUlyHzXuId9Rw9bOS1fRwA6k4cC0qqWDO3d12ojN5B9Tr1IPV65weu7sCQT0PzkUKI0KeCoAGcPy0+ibk4VxL'

HEADERS = {'User-Agent': 'foo', 'Content-Type': 'text/xml'}
BS = 16

# how many quotes are we allowed to verify before asking the registrar if the key is valid
MAX_STALE_REGISTRAR_CACHE = 200

if STUB_IMA:
    IMA_ML = '../scripts/ima/ascii_runtime_measurements'
else:
    IMA_ML = '/sys/kernel/security/ima/ascii_runtime_measurements'
    
IMA_PCR = 10

# this is where data will be bound to a quote, MUST BE RESETABLE!
TPM_DATA_PCR = 16

# the size of the bootstrap key for AES-GCM 256bit
BOOTSTRAP_KEY_SIZE=32

# choose between cfssl or openssl for creating CA certificates
CA_IMPL = config.get('general','ca_implementation')

CRL_PORT=38080
