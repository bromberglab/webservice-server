# Generated by Django 2.2.6 on 2019-11-25 19:14

from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0027_permissions'),
    ]

    operations = [
        migrations.AddField(
            model_name='nodeimage',
            name='added_at',
            field=models.DateTimeField(auto_now_add=True, default=django.utils.timezone.now),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='nodeimage',
            name='updated_at',
            field=models.DateTimeField(auto_now=True),
        ),
    ]
