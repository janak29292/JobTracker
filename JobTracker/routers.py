

class ModuleRouter:
    # Tables that exist ONLY in 'job'
    JOB_APPS = {'job'}

    # Tables that exist in BOTH 'default' and 'job'
    # SHARED_APPS = {'auth', 'contenttypes', 'my_main_app'}

    def db_for_read(self, model, **hints):
        if model._meta.app_label in self.JOB_APPS:
            return 'job'
        # For shared tables, you must choose a "primary" for reads
        # or use logic (like hints) to decide.
        return 'default'

    def db_for_write(self, model, **hints):
        if model._meta.app_label in self.JOB_APPS:
            return 'job'
        return 'default'

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if app_label in self.JOB_APPS:
            return db == 'job'
        # if app_label in self.SHARED_APPS:
        #     # This allows these tables to be created in BOTH databases
        #     return db in ['default', 'job']
        return db == 'default'
