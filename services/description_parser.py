"""
Enhanced parser using spaCy for better NLP
Install: pip install spacy
Download model: python -m spacy download en_core_web_sm
"""

import re
import spacy
from typing import Dict, List, Optional
from collections import Counter


class AdvancedJobParser:
    """Advanced job description parser using spaCy"""

    def __init__(self):
        try:
            # Load spaCy model
            self.nlp = spacy.load("en_core_web_sm")
        except OSError:
            print("spaCy model not found. Run: python -m spacy download en_core_web_sm")
            self.nlp = None

        # Tech keywords database
        self.tech_keywords = self._load_tech_keywords()

    def _load_tech_keywords(self) -> Dict[str, List[str]]:
        """Load comprehensive tech keywords"""
        return {
            'languages': [
                'python', 'java', 'javascript', 'typescript', 'c++', 'c#', 'csharp',
                'go', 'golang', 'ruby', 'php', 'swift', 'kotlin', 'rust', 'scala',
                'r', 'matlab', 'perl', 'shell', 'bash', 'powershell', 'dart',
                'lua', 'elixir', 'haskell', 'groovy', 'clojure', 'objective-c',
                'solidity', 'zig', 'julia', 'fortran', 'cobol', 'sql', 'plsql',
                'pl/sql', 'vba', 'assembly'
            ],
            'frameworks': [
                'django', 'flask', 'fastapi', 'spring', 'spring boot', 'react',
                'reactjs', 'angular', 'vue', 'vue.js', 'vuejs', 'node.js', 'nodejs',
                'express', 'expressjs', 'next.js', 'nextjs', 'nuxt', 'laravel',
                'rails', 'ruby on rails', '.net', 'dotnet', 'asp.net', 'blazor',
                'svelte', 'ember', 'backbone', 'meteor',
                'nestjs', 'nest.js', 'fastify', 'hapi', 'koa', 'gin', 'fiber',
                'echo', 'phoenix', 'quarkus', 'micronaut', 'ktor', 'actix',
                'rocket', 'deno', 'bun', 'remix', 'gatsby', 'astro',
                'starlette', 'sanic', 'tornado', 'aiohttp', 'bottle',
                'dropwizard', 'play framework', 'struts', 'hibernate',
                'celery', 'dramatiq', 'huey'
            ],
            'databases': [
                'mysql', 'postgresql', 'postgres', 'mongodb', 'mongo', 'redis',
                'cassandra', 'oracle', 'sql server', 'mssql', 'sqlite', 'dynamodb',
                'elasticsearch', 'mariadb', 'couchdb', 'neo4j', 'firebase',
                'firestore', 'couchbase', 'influxdb', 'timescaledb',
                'cockroachdb', 'snowflake', 'redshift', 'bigquery', 'clickhouse',
                'supabase', 'planetscale', 'fauna', 'faunadb', 'memcached',
                'opensearch', 'solr', 'arangodb', 'dgraph', 'scylladb',
                'vitess', 'tidb', 'yugabytedb', 'etcd', 'amazon aurora',
                'cosmos db', 'cosmosdb', 'documentdb'
            ],
            'cloud': [
                'aws', 'amazon web services', 'azure', 'microsoft azure', 'gcp',
                'google cloud', 'google cloud platform', 'heroku', 'digitalocean',
                'linode', 'cloudflare', 'vercel', 'netlify', 's3', 'ec2', 'lambda',
                'rds', 'cloudfront', 'route53',
                'eks', 'ecs', 'fargate', 'sagemaker', 'glue', 'athena', 'emr',
                'kinesis', 'step functions', 'eventbridge', 'api gateway',
                'cloud run', 'cloud functions', 'cloud storage', 'app engine',
                'azure functions', 'azure devops', 'aks', 'blob storage',
                'oracle cloud', 'oci', 'alibaba cloud', 'ibm cloud',
                'elastic beanstalk', 'lightsail', 'amplify',
                'cloudwatch', 'cloud formation', 'cloudformation', 'cdk'
            ],
            'devops': [
                'docker', 'kubernetes', 'k8s', 'jenkins', 'gitlab ci', 'github actions',
                'terraform', 'ansible', 'chef', 'puppet', 'circleci', 'travis ci',
                'bamboo', 'octopus', 'spinnaker', 'argo', 'flux', 'helm',
                'vagrant', 'packer', 'pulumi', 'crossplane', 'istio', 'envoy',
                'linkerd', 'consul', 'vault', 'nomad', 'rancher', 'openshift',
                'tekton', 'drone ci', 'buildkite', 'harness', 'argocd',
                'skaffold', 'kustomize', 'podman', 'containerd',
                'sonarqube', 'nexus', 'artifactory', 'trivy', 'snyk'
            ],
            'tools': [
                'git', 'github', 'gitlab', 'bitbucket', 'jira', 'confluence',
                'postman', 'swagger', 'insomnia', 'vs code', 'visual studio',
                'intellij', 'pycharm', 'eclipse', 'vim', 'emacs', 'sublime',
                'notion', 'slack', 'trello', 'asana', 'linear', 'figma',
                'datadog', 'new relic', 'splunk', 'pagerduty', 'opsgenie',
                'openapi', 'graphiql', 'wireshark', 'fiddler',
                'terminal', 'tmux', 'zsh', 'warp', 'docker desktop'
            ],
            'api': [
                'rest', 'rest api', 'restful', 'graphql', 'grpc', 'soap',
                'websocket', 'api gateway', 'microservices', 'json', 'xml',
                'protobuf', 'protocol buffers', 'thrift', 'avro', 'openapi',
                'swagger', 'postman', 'webhooks', 'server-sent events', 'sse',
                'jsonapi', 'odata', 'hateoas', 'rpc', 'mqtt', 'amqp'
            ],
            'frontend': [
                'html', 'html5', 'css', 'css3', 'sass', 'scss', 'less', 'bootstrap',
                'tailwind', 'tailwindcss', 'material ui', 'mui', 'webpack', 'vite',
                'rollup', 'parcel', 'gulp', 'grunt', 'jquery', 'redux', 'mobx',
                'recoil', 'zustand',
                'chakra ui', 'ant design', 'antd', 'styled-components', 'emotion',
                'shadcn', 'radix', 'headless ui', 'storybook', 'turborepo',
                'pnpm', 'yarn', 'npm', 'esbuild', 'swc', 'babel', 'prettier',
                'eslint', 'stylelint', 'postcss', 'three.js', 'threejs', 'd3',
                'd3.js', 'chart.js', 'recharts', 'framer motion', 'gsap',
                'react query', 'tanstack', 'swr', 'axios', 'pwa'
            ],
            'data_science': [
                'pandas', 'numpy', 'scikit-learn', 'sklearn', 'tensorflow', 'pytorch',
                'keras', 'opencv', 'nltk', 'spacy', 'jupyter', 'matplotlib', 'seaborn',
                'plotly', 'spark', 'pyspark', 'hadoop', 'airflow', 'dask', 'rapids',
                'xgboost', 'lightgbm', 'catboost', 'hugging face', 'huggingface',
                'transformers', 'langchain', 'llamaindex', 'openai', 'gpt',
                'bert', 'llm', 'rag', 'vector database', 'pinecone', 'weaviate',
                'milvus', 'chroma', 'qdrant', 'faiss', 'stable diffusion',
                'mlflow', 'wandb', 'optuna', 'ray', 'polars', 'vaex',
                'scipy', 'statsmodels', 'prophet', 'databricks', 'snowpark',
                'feature store', 'feast', 'great expectations'
            ],
            'testing': [
                'pytest', 'unittest', 'jest', 'mocha', 'chai', 'jasmine', 'selenium',
                'cypress', 'playwright', 'puppeteer', 'junit', 'testng', 'cucumber',
                'behave', 'robot framework',
                'vitest', 'testing library', 'react testing library', 'enzyme',
                'mock', 'mockito', 'powermock', 'wiremock', 'locust', 'gatling',
                'k6', 'jmeter', 'artillery', 'postman tests', 'supertest',
                'hypothesis', 'faker', 'factory boy', 'nunit', 'xunit',
                'appium', 'detox', 'storybook', 'chromatic', 'percy',
                'sonarqube', 'codecov', 'coveralls'
            ],
            'messaging': [
                'rabbitmq', 'kafka', 'apache kafka', 'redis pub/sub', 'celery',
                'sqs', 'sns', 'google pub/sub', 'nats', 'activemq',
                'zeromq', 'mqtt', 'amazon msk', 'confluent', 'pulsar',
                'apache pulsar', 'amazon kinesis', 'event hub', 'azure service bus',
                'redis streams', 'bull', 'bullmq', 'sidekiq', 'resque'
            ],
            'web_servers': [
                'nginx', 'apache', 'apache tomcat', 'iis', 'gunicorn', 'uwsgi',
                'passenger', 'puma',
                'caddy', 'traefik', 'haproxy', 'envoy', 'uvicorn', 'hypercorn',
                'daphne', 'lighttpd', 'varnish'
            ],
            'security': [
                'oauth', 'oauth2', 'jwt', 'json web token', 'saml', 'sso',
                'single sign-on', 'ldap', 'active directory', 'keycloak',
                'auth0', 'okta', 'cognito', 'firebase auth', 'owasp',
                'ssl', 'tls', 'https', 'cors', 'csrf', 'xss',
                'encryption', 'hashing', 'bcrypt', 'argon2',
                'rbac', 'abac', 'iam', 'kms', 'secrets manager',
                'vault', 'certbot', 'letsencrypt', 'mfa', '2fa',
                'penetration testing', 'sast', 'dast', 'snyk', 'trivy',
                'sonarqube', 'checkmarx', 'fortify'
            ],
            'monitoring': [
                'prometheus', 'grafana', 'datadog', 'new relic', 'elk stack',
                'elasticsearch', 'logstash', 'kibana', 'splunk', 'nagios',
                'zabbix', 'dynatrace', 'appdynamics', 'cloudwatch',
                'sentry', 'rollbar', 'bugsnag', 'pagerduty', 'opsgenie',
                'jaeger', 'zipkin', 'opentelemetry', 'otel', 'fluentd',
                'fluentbit', 'loki', 'tempo', 'mimir', 'thanos',
                'victoriametrics', 'signoz', 'uptimerobot'
            ],
            'mobile': [
                'react native', 'flutter', 'swiftui', 'jetpack compose',
                'xamarin', 'ionic', 'cordova', 'capacitor', 'expo',
                'kotlin multiplatform', 'kmp', 'maui', '.net maui'
            ],
            'big_data': [
                'spark', 'pyspark', 'hadoop', 'hive', 'hbase', 'pig',
                'flink', 'apache flink', 'beam', 'apache beam',
                'presto', 'trino', 'druid', 'apache druid',
                'nifi', 'apache nifi', 'sqoop', 'flume', 'oozie',
                'delta lake', 'iceberg', 'apache iceberg', 'hudi',
                'dbt', 'fivetran', 'airbyte', 'stitch', 'talend',
                'informatica', 'datastax', 'cloudera', 'hortonworks',
                'databricks', 'palantir', 'redpanda'
            ]
        }

    def parse(self, description: str) -> Dict:
        """Parse job description comprehensively"""
        # Use spaCy if available
        if self.nlp:
            doc = self.nlp(description)
            entities = self._extract_entities(doc)
        else:
            entities = {}

        return {
            'experience': self.extract_experience(description),
            'tech_stack': self.extract_tech_stack(description),
            'education': self.extract_education(description),
            'job_type': self.extract_job_type(description),
            'salary': self.extract_salary(description),
            'location': entities.get('location', []),
            'skills_summary': self.get_skills_summary(description)
        }

    def _extract_entities(self, doc) -> Dict:
        """Extract named entities using spaCy"""
        entities = {
            'location': [],
            'organization': [],
            'money': []
        }

        for ent in doc.ents:
            if ent.label_ == 'GPE':  # Geo-political entity (location)
                entities['location'].append(ent.text)
            elif ent.label_ == 'ORG':  # Organization
                entities['organization'].append(ent.text)
            elif ent.label_ == 'MONEY':  # Money
                entities['money'].append(ent.text)

        return entities

    def extract_experience(self, description: str) -> Dict:
        """Extract experience with multiple patterns"""
        desc_lower = description.lower()

        patterns = [
            r'(\d+)\s*(?:\+|plus)?\s*(?:to|\-|–)\s*(\d+)?\s*(?:years?|yrs?)',
            r'(?:minimum|min|at least|atleast)\s*(\d+)\s*(?:years?|yrs?)',
            r'(?:maximum|max|up to|upto)\s*(\d+)\s*(?:years?|yrs?)',
            r'(\d+)\s*\+\s*(?:years?|yrs?)',
            r'experience\s*:?\s*(\d+)\s*(?:\-|to)?\s*(\d+)?\s*(?:years?|yrs?)?',
        ]

        min_exp = None
        max_exp = None
        is_fresher = bool(re.search(r'\b(?:fresher|freshers?|0\s*(?:\-|to)\s*1\s*year)\b', desc_lower))

        if is_fresher:
            min_exp = 0
            max_exp = 1

        for pattern in patterns:
            matches = re.finditer(pattern, desc_lower)
            for match in matches:
                groups = [g for g in match.groups() if g]
                numbers = [int(g) for g in groups if g.isdigit()]

                if numbers:
                    if min_exp is None:
                        min_exp = min(numbers)
                    else:
                        min_exp = min(min_exp, min(numbers))

                    if max_exp is None:
                        max_exp = max(numbers)
                    else:
                        max_exp = max(max_exp, max(numbers))

        # Calculate experience level
        level = 'unknown'
        if min_exp is not None:
            if min_exp == 0:
                level = 'fresher'
            elif min_exp <= 2:
                level = 'junior'
            elif min_exp <= 5:
                level = 'mid-level'
            elif min_exp <= 8:
                level = 'senior'
            else:
                level = 'lead/principal'

        return {
            'min_years': min_exp,
            'max_years': max_exp,
            'is_fresher': is_fresher,
            'level': level
        }

    def extract_tech_stack(self, description: str) -> Dict[str, List[str]]:
        """Extract technologies with scoring"""
        desc_lower = description.lower()
        found_tech = {}
        tech_scores = Counter()

        for category, keywords in self.tech_keywords.items():
            found = []
            for keyword in keywords:
                # Use word boundaries
                pattern = r'\b' + re.escape(keyword.lower()) + r'\b'
                matches = re.findall(pattern, desc_lower)

                if matches:
                    found.append(keyword)
                    # Score based on frequency
                    tech_scores[keyword] = len(matches)

            if found:
                found_tech[category] = found

        # Get all unique technologies
        all_tech = []
        for techs in found_tech.values():
            all_tech.extend(techs)

        found_tech['all'] = list(set(all_tech))
        found_tech['primary'] = [tech for tech, count in tech_scores.most_common(5)]

        return found_tech

    def extract_education(self, description: str) -> Dict:
        """Extract education requirements"""
        desc_lower = description.lower()

        education_patterns = {
            'bachelors': r'bachelor\W?s?\W?\s*(?:degree)?|b\.?tech|b\.?e\.?|btech|be\b|b\.?sc|bsc',
            'masters': r'master\W?s?\W?\s*(?:degree)?|m\.?tech|m\.?e\.?|mtech|me\b|m\.?sc|msc',
            'phd': r'phd|ph\.?d\.?|doctorate',
            'diploma': r'diploma',
            'certification': r'certification|certified|certificate'
        }

        found = {}
        for edu_level, pattern in education_patterns.items():
            if re.search(pattern, desc_lower):
                found[edu_level] = True

        # Check if CS/IT specific
        cs_related = bool(re.search(
            r'computer science|cs\b|information technology|it\b|software engineering',
            desc_lower
        ))

        return {
            'degrees': found,
            'cs_related': cs_related,
            'required': bool(re.search(r'(?:required|must have|mandatory).*(?:degree|education)', desc_lower))
        }

    def extract_job_type(self, description: str) -> Dict:
        """Extract job type with confidence"""
        desc_lower = description.lower()

        return {
            'remote': bool(re.search(r'\b(?:remote|work from home|wfh|distributed)\b', desc_lower)),
            'onsite': bool(re.search(r'\b(?:onsite|on-site|office|in-office)\b', desc_lower)),
            'hybrid': bool(re.search(r'\bhybrid\b', desc_lower)),
            'full_time': bool(re.search(r'\b(?:full time|full-time|fulltime)\b', desc_lower)),
            'part_time': bool(re.search(r'\b(?:part time|part-time|parttime)\b', desc_lower)),
            'contract': bool(re.search(r'\b(?:contract|contractor|freelance|consultant)\b', desc_lower)),
            'internship': bool(re.search(r'\b(?:intern|internship)\b', desc_lower))
        }

    def extract_salary(self, description: str) -> Optional[Dict]:
        """Extract salary information"""
        # Patterns for salary
        salary_patterns = [
            r'(\d+)\s*(?:lpa|lac|lakhs?)\s*(?:to|\-)\s*(\d+)\s*(?:lpa|lac|lakhs?)',
            r'₹\s*(\d+(?:,\d+)*)\s*(?:to|\-)\s*₹?\s*(\d+(?:,\d+)*)',
            r'\$\s*(\d+(?:,\d+)*)\s*(?:to|\-)\s*\$?\s*(\d+(?:,\d+)*)',
            r'(\d+)\s*(?:to|\-)\s*(\d+)\s*(?:k|thousand)',
        ]

        for pattern in salary_patterns:
            match = re.search(pattern, description, re.IGNORECASE)
            if match:
                return {
                    'min': match.group(1),
                    'max': match.group(2) if len(match.groups()) > 1 else None,
                    'raw_text': match.group(0)
                }

        return None

    def get_skills_summary(self, description: str) -> Dict:
        """Get a summary of required vs preferred skills"""
        desc_lower = description.lower()

        # Split by sections
        required_section = re.search(
            r'(?:required|must have|mandatory).*?(?=(?:preferred|nice to have|plus|$))',
            desc_lower,
            re.DOTALL
        )

        preferred_section = re.search(
            r'(?:preferred|nice to have|plus|bonus).*?(?=$)',
            desc_lower,
            re.DOTALL
        )

        return {
            'has_required_section': bool(required_section),
            'has_preferred_section': bool(preferred_section),
            'total_tech_count': len(self.extract_tech_stack(description).get('all', []))
        }


# Usage
if __name__ == "__main__":
    parser = AdvancedJobParser()

    job_desc = """
    Senior Python Developer - Remote

    We're looking for a talented developer with 5-7 years of experience.

    Required Skills:
    - Expert in Python, Django, and FastAPI
    - Strong PostgreSQL and Redis experience
    - AWS services (EC2, S3, Lambda)
    - Docker and Kubernetes

    Preferred:
    - React or Vue.js
    - Experience with Kafka or RabbitMQ
    - CI/CD with GitHub Actions

    Education: Bachelor's in Computer Science or equivalent
    Salary: 15-25 LPA
    Location: Bangalore (Hybrid)
    """

    result = parser.parse(job_desc)

    import json

    print(json.dumps(result, indent=2))
