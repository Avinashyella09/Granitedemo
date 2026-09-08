import datetime
from mongoengine import Document, EmbeddedDocument, fields, NULLIFY, CASCADE

class Quarry(Document):
    id = fields.StringField(primary_key=True, max_length=100)
    name = fields.StringField(required=True, max_length=255)
    location = fields.StringField(max_length=500) # details/location info
    created_at = fields.DateTimeField(default=datetime.datetime.utcnow)
    
    # Analytics Extensions
    district = fields.StringField(max_length=100)
    region = fields.StringField(max_length=100)
    lessee_name = fields.StringField(max_length=255)
    total_area_hectares = fields.FloatField()
    registered_date = fields.DateTimeField()
    license_expiry_date = fields.DateTimeField()

    meta = {
        'collection': 'quarries',
        'indexes': ['name']
    }

    def __str__(self):
        return self.name

class Officer(Document):
    officer_id = fields.StringField(required=True, unique=True, max_length=100)
    name = fields.StringField(required=True, max_length=255)
    designation = fields.StringField(max_length=100)
    assigned_quarries = fields.ListField(fields.ReferenceField(Quarry, reverse_delete_rule=NULLIFY))
    phone = fields.StringField(max_length=50)
    email = fields.StringField(max_length=100)
    joined_date = fields.DateTimeField(default=datetime.datetime.utcnow)
    active_status = fields.BooleanField(default=True)

    meta = {
        'collection': 'officers',
        'indexes': ['officer_id']
    }

    def __str__(self):
        return self.name

class Measurement(EmbeddedDocument):
    length_m = fields.FloatField(required=True)
    breadth_m = fields.FloatField(required=True)
    height_m = fields.FloatField(required=True)
    volume_m3 = fields.FloatField(required=True)
    confidence = fields.FloatField(default=1.0)
    measurement_method = fields.StringField(default='manual', max_length=50) # 'cv', 'manual'
    measured_at = fields.DateTimeField(default=datetime.datetime.utcnow)

class Block(Document):
    block_id = fields.StringField(required=True, unique=True, max_length=100)
    quarry = fields.ReferenceField(Quarry, reverse_delete_rule=NULLIFY)
    image_paths = fields.ListField(fields.StringField(max_length=500))
    captured_at = fields.DateTimeField()
    gps_latitude = fields.FloatField()
    gps_longitude = fields.FloatField()
    status = fields.StringField(default='pending', max_length=50) # e.g. 'pending', 'measured', 'assessed'
    measurement = fields.EmbeddedDocumentField(Measurement)
    cv_status = fields.StringField(default='pending', max_length=50)
    cv_error_message = fields.StringField(max_length=500)
    raw_image_path = fields.StringField(max_length=500)
    annotated_image_path = fields.StringField(max_length=500)
    is_overridden = fields.BooleanField(default=False)
    original_measurement = fields.EmbeddedDocumentField(Measurement)
    override_reason = fields.StringField(max_length=500)
    approval_status = fields.StringField(default='pending', max_length=50)
    approved_by = fields.StringField(max_length=100)
    approved_at = fields.DateTimeField()
    created_at = fields.DateTimeField(default=datetime.datetime.utcnow)
    updated_at = fields.DateTimeField(default=datetime.datetime.utcnow)
    
    # Analytics Extensions
    inspecting_officer_id = fields.StringField(max_length=100)
    inspection_duration_seconds = fields.IntField(default=0)
    capture_attempt_count = fields.IntField(default=1)
    lighting_condition = fields.StringField(max_length=50)
    device_id = fields.StringField(max_length=100)

    meta = {
        'collection': 'blocks',
        'indexes': ['block_id', 'status', 'inspecting_officer_id']
    }

    def __str__(self):
        return self.block_id

    def save(self, *args, **kwargs):
        self.updated_at = datetime.datetime.utcnow()
        return super(Block, self).save(*args, **kwargs)

class Assessment(Document):
    block = fields.ReferenceField(Block, required=True, reverse_delete_rule=CASCADE)
    granite_category = fields.StringField(required=True, max_length=100) # e.g. 'Premium', 'Standard'
    gangsaw_classification = fields.StringField(required=True, max_length=100) # e.g. 'Gangsaw Size', 'Mini Gangsaw Size'
    volume_m3 = fields.FloatField(required=True)
    weight_mt = fields.FloatField(required=True)
    rate_per_mt = fields.FloatField(required=True) # POC rate per metric tonne
    indicative_seigniorage = fields.FloatField(required=True)
    density_mt_per_m3 = fields.FloatField(required=True)
    weighbridge_weight_mt = fields.FloatField() # optional
    variance_pct = fields.FloatField() # optional
    dispatch_return_weight_mt = fields.FloatField() # optional
    dispatch_variance_pct = fields.FloatField() # optional
    status = fields.StringField(default='draft', max_length=50) # 'draft', 'finalized'
    created_at = fields.DateTimeField(default=datetime.datetime.utcnow)
    
    # Analytics Extensions
    expected_vs_actual_variance_pct = fields.FloatField()
    revenue_bucket = fields.StringField(max_length=50)
    assessment_week = fields.IntField()
    assessment_month = fields.IntField()

    meta = {
        'collection': 'assessments',
        'indexes': ['block']
    }

class AuditLog(Document):
    block = fields.ReferenceField(Block, reverse_delete_rule=CASCADE)
    action = fields.StringField(required=True, max_length=100)
    actor = fields.StringField(required=True, max_length=100)
    details = fields.StringField() # detailed JSON string or description
    timestamp = fields.DateTimeField(default=datetime.datetime.utcnow)
    
    # Analytics Extensions
    sla_breach = fields.BooleanField(default=False)
    severity = fields.StringField(default='INFO', max_length=50) # 'INFO', 'WARNING', 'CRITICAL'

    meta = {
        'collection': 'audit_logs',
        'indexes': ['block', 'timestamp']
    }

class WeeklyOfficerSummary(Document):
    officer_id = fields.StringField(required=True, max_length=100)
    week_start_date = fields.DateTimeField(required=True)
    blocks_inspected = fields.IntField(default=0)
    avg_confidence = fields.FloatField(default=0.0)
    override_count = fields.IntField(default=0)
    approval_rate = fields.FloatField(default=0.0)
    avg_inspection_duration_seconds = fields.FloatField(default=0.0)

    meta = {
        'collection': 'weekly_officer_summaries',
        'indexes': ['officer_id', 'week_start_date']
    }

