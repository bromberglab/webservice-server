# Generated by Django 2.2.8 on 2019-12-12 22:16

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0038_job_finished_runs'),
    ]

    operations = [
        migrations.AddField(
            model_name='job',
            name='should_notify',
            field=models.BooleanField(default=False),
        ),
    ]
