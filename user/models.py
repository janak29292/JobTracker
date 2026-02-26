from django.contrib.auth.models import AbstractUser
from django.db import models
from bs4 import BeautifulSoup

# Create your models here.


# Create your models here.
class User(AbstractUser):
    pass


class Unstructured(models.Model):
    parent = models.ForeignKey(
        'self', on_delete=models.CASCADE,
        null=True, related_name='children'
    )
    info = models.TextField()

    def __str__(self):
        if self.parent:
            return f"{self.parent.__str__()}: {BeautifulSoup(self.info, 'html.parser').get_text()}"
        return BeautifulSoup(self.info, 'html.parser').get_text()


class Category(models.Model):
    name = models.CharField(max_length=100)

    def __str__(self):
        return self.name


class Pattern(models.Model):
    name = models.CharField(max_length=100)
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name='patterns')
    description = models.TextField()
    use_cases = models.CharField(max_length=100, null=True, blank=True)

    def __str__(self):
        return self.name

class Problem(models.Model):
    pattern = models.ForeignKey(Pattern, on_delete=models.CASCADE, related_name='problems')
    phrase = models.CharField(max_length=100)
    statement = models.TextField()

    def __str__(self):
        return self.phrase

class Approach(models.Model):
    name = models.CharField(max_length=100)
    pattern = models.ForeignKey(Pattern, on_delete=models.CASCADE, related_name='approaches')
    description = models.TextField()
    time_complexity = models.CharField(max_length=10, null=True, blank=True)
    space_complexity = models.CharField(max_length=10, null=True, blank=True)
    code_example = models.TextField()
    code_result = models.TextField()

    def __str__(self):
        return self.name
