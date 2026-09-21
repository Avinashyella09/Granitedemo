from django.urls import path
from .views import (
    AuthMeAPIView,
    AuthCsrfAPIView,
    AuthLoginAPIView,
    AuthLogoutAPIView,
    BlockListCreateAPIView, 
    BlockDetailAPIView, 
    AssessmentListCreateAPIView, 
    AssessmentDetailAPIView,
    BlockMeasureCVAPIView,
    BlockARMeasureAPIView,
    BlockOverrideAPIView,
    BlockApproveAPIView,
    BlockPDFAPIView,
    BlockAuditLogsAPIView,
    OMEPSExportAPIView,
    
    # Analytics views
    OfficerAnalyticsAPIView,
    OfficerWeeklyAnalyticsAPIView,
    QuarryComparisonAPIView,
    QuarryTrendAPIView,
    RevenueSummaryAPIView,
    RevenueLeakageAPIView,
    AuditReadinessAPIView,
    AnalyticsAlertsAPIView,
    MapDataAPIView,
    ExecutiveOverviewAPIView
)

urlpatterns = [
    path('blocks/', BlockListCreateAPIView.as_view(), name='block-list-create'),
    path('blocks/ar-measure/', BlockARMeasureAPIView.as_view(), name='block-ar-measure'),
    path('blocks/<str:block_id>/', BlockDetailAPIView.as_view(), name='block-detail'),
    path('blocks/<str:block_id>/measure-cv/', BlockMeasureCVAPIView.as_view(), name='block-measure-cv'),
    path('blocks/<str:block_id>/override/', BlockOverrideAPIView.as_view(), name='block-override'),
    path('blocks/<str:block_id>/approve/', BlockApproveAPIView.as_view(), name='block-approve'),
    path('blocks/<str:block_id>/pdf/', BlockPDFAPIView.as_view(), name='block-pdf'),
    path('blocks/<str:block_id>/audit-logs/', BlockAuditLogsAPIView.as_view(), name='block-audit-logs'),
    path('assessments/', AssessmentListCreateAPIView.as_view(), name='assessment-list-create'),
    path('assessments/<str:block_id>/', AssessmentDetailAPIView.as_view(), name='assessment-detail'),
    
    # Analytics API routes
    path('analytics/officers/', OfficerAnalyticsAPIView.as_view(), name='analytics-officers'),
    path('analytics/officers/<str:officer_id>/weekly/', OfficerWeeklyAnalyticsAPIView.as_view(), name='analytics-officer-weekly'),
    path('analytics/quarries/comparison/', QuarryComparisonAPIView.as_view(), name='analytics-quarries-comparison'),
    path('analytics/quarries/<str:quarry_id>/trend/', QuarryTrendAPIView.as_view(), name='analytics-quarry-trend'),
    path('analytics/revenue/summary/', RevenueSummaryAPIView.as_view(), name='analytics-revenue-summary'),
    path('analytics/revenue/leakage/', RevenueLeakageAPIView.as_view(), name='analytics-revenue-leakage'),
    path('analytics/audit-readiness/', AuditReadinessAPIView.as_view(), name='analytics-audit-readiness'),
    path('analytics/alerts/', AnalyticsAlertsAPIView.as_view(), name='analytics-alerts'),
    path('analytics/map-data/', MapDataAPIView.as_view(), name='analytics-map-data'),
    path('analytics/overview/', ExecutiveOverviewAPIView.as_view(), name='analytics-overview'),
    # OMEPS-ready export / integration contract - pending official OMEPS schema.
    path('export/omeps/<str:block_id>/', OMEPSExportAPIView.as_view(), name='omeps-export'),

    # Phase 6E: dashboard signs in with username/password and gets a Django
    # session. Token auth stays available for API clients (see settings).
    path('auth/csrf/', AuthCsrfAPIView.as_view(), name='auth-csrf'),
    path('auth/login/', AuthLoginAPIView.as_view(), name='auth-login'),
    path('auth/logout/', AuthLogoutAPIView.as_view(), name='auth-logout'),
    path('auth/me/', AuthMeAPIView.as_view(), name='auth-me'),
]
