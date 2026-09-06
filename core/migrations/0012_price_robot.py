import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0011_align_billing_usersubscription'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='PriceRobotRun',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('mode', models.CharField(choices=[('plan', 'План (dry-run)'), ('apply', 'Применение цен')], default='plan', max_length=16)),
                ('status', models.CharField(choices=[('running', 'Running'), ('success', 'Success'), ('error', 'Error')], default='running', max_length=16)),
                ('season_phase', models.CharField(blank=True, default='', max_length=32)),
                ('summary', models.JSONField(blank=True, default=dict)),
                ('result', models.JSONField(blank=True, default=dict)),
                ('error', models.TextField(blank=True, default='')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('finished_at', models.DateTimeField(blank=True, null=True)),
                ('seller', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='price_robot_runs', to='core.selleraccount')),
                ('user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='PricingPolicy',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('nm_id', models.BigIntegerField()),
                ('vendor_code', models.CharField(blank=True, default='', max_length=255)),
                ('enabled', models.BooleanField(default=True)),
                ('mode', models.CharField(choices=[('off', 'Выключен'), ('dry_run', 'Только план (dry-run)'), ('auto', 'Авто (пишет цены в WB)')], default='dry_run', max_length=16)),
                ('target_zero_date', models.DateField(blank=True, null=True)),
                ('incoming_qty', models.IntegerField(default=0)),
                ('purchase_price', models.FloatField(blank=True, null=True)),
                ('buyout_rate', models.FloatField(blank=True, null=True)),
                ('floor_price', models.FloatField(blank=True, null=True)),
                ('ceiling_price', models.FloatField(blank=True, null=True)),
                ('max_step_up_points', models.IntegerField(blank=True, null=True)),
                ('max_step_down_points', models.IntegerField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('seller', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='pricing_policies', to='core.selleraccount')),
            ],
        ),
        migrations.AddIndex(
            model_name='pricerobotrun',
            index=models.Index(fields=['seller', 'created_at'], name='core_pricer_seller__859450_idx'),
        ),
        migrations.AddIndex(
            model_name='pricerobotrun',
            index=models.Index(fields=['seller', 'mode', 'created_at'], name='core_pricer_seller__3d0bc4_idx'),
        ),
        migrations.AddIndex(
            model_name='pricingpolicy',
            index=models.Index(fields=['seller', 'nm_id'], name='core_pricin_seller__1d2426_idx'),
        ),
        migrations.AddIndex(
            model_name='pricingpolicy',
            index=models.Index(fields=['seller', 'enabled'], name='core_pricin_seller__05dff9_idx'),
        ),
        migrations.AlterUniqueTogether(
            name='pricingpolicy',
            unique_together={('seller', 'nm_id')},
        ),
    ]
