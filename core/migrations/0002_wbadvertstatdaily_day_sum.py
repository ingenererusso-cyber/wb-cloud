from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="wbadvertstatdaily",
            name="day_sum",
            field=models.FloatField(default=0.0),
        ),
    ]
