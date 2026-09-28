import os

LINKEDIN_USERNAME = os.getenv("LINKEDIN_USERNAME")
LINKEDIN_PASSWORD = os.getenv("LINKEDIN_PASSWORD")
LINKEDIN_LOGIN_URL = 'https://www.linkedin.com/login'
LINKEDIN_RECOMMENDED = (
    'https://www.linkedin.com/jobs/collections/recommended/?'
    'discover=recommended'
    '&discoveryOrigin=JOBS_HOME_JYMBII'
)

LINKEDIN_FILTERED = (
    "https://www.linkedin.com/jobs/search/?"
    "&f_T=39%2C25169%2C25194"
    "&f_WT=1%2C3"
    "&f_TPR=r86400"
)

LINKEDIN_DATE_POSTED = {
    "&f_TPR=r86400": "Past 24 hours",
    "&f_TPR=r604800": "Past week",
    "&f_TPR=r2592000": "Past month"
}


LINKEDIN_LOCATIONS = {
    "&geoId=115884833": "Gurugram, Haryana, India",
    "&geoId=106187582": "Delhi, India",
    "&geoId=115918471": "New Delhi, Delhi, India",
    "&geoId=104869687": "Noida, Uttar Pradesh, India",
    "&geoId=90009626": "Greater Delhi Area"
}

LINKEDIN_LOCATIONS_2 = {
    "&f_PP=104793846,", # gURGAON
    "&f_PP=106442238", # GURUGRAM
    "&f_PP=104869687", # nOIDA
}

LINKEDIN_LOCATIONS_2_STR = "&f_PP=104793846,106442238,104869687"


POSSIBLE_JOB_IDENTIFIERS = [
    'data-occludable-job-id',
    'data-job-id',
    'data-entity-urn',
]

FIBONACCI_WAITS = [3, 5, 8]


NAUKRI_LOGIN_URL = "https://www.naukri.com/nlogin/login"

NAUKRI_USERNAME = os.getenv("NAUKRI_USERNAME")
NAUKRI_PASSWORD = os.getenv("NAUKRI_PASSWORD")

NAUKRI_FILTERED = (
    "https://www.naukri.com/python-developer-jobs-in-delhi%2c-noida%2c-gurgaon%2fgurugram?"
    "experience=5"
    "&jobAge=1"
)

NAUKRI_FILTERED_2 = (
    "https://www.naukri.com/python-django-senior-jobs?"
    "experience=5"
    "&cityTypeGid=6"
    "&cityTypeGid=72"
    "&cityTypeGid=73"
    "&cityTypeGid=213"
    "&cityTypeGid=220"
    "&cityTypeGid=350"
    "&cityTypeGid=9508"
    "&jobAge=1"
    # "&functionAreaIdGid=3"
    "&functionAreaIdGid=5"
    # "&glbl_qcrc=1019"
    # "&glbl_qcrc=1020"
    # "&glbl_qcrc=1025"
    # "&glbl_qcrc=1026"
    "&glbl_qcrc=1028"
    "&ctcFilter=10to15"
    "&ctcFilter=25to50"
    "&ctcFilter=15to25"
)
