# Generated by Django 2.2.8 on 2020-02-12 20:28

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0054_auto_20200212_1618'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='globals',
            options={'permissions': [('is_guest_user', 'This user has the status Guest.')]},
        ),
    ]
