# Generated by Django 2.2.6 on 2019-11-28 03:55

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0028_auto_20191125_1914'),
    ]

    operations = [
        migrations.AddField(
            model_name='nodeimage',
            name='override_string',
            field=models.TextField(default='{}'),
        ),
    ]