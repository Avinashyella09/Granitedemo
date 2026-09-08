import os
from io import BytesIO
from django.conf import settings
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

def generate_block_pdf(block, assessment=None):
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, 
        pagesize=letter,
        rightMargin=36, 
        leftMargin=36, 
        topMargin=36, 
        bottomMargin=36
    )
    
    story = []
    styles = getSampleStyleSheet()
    
    # Custom styles
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Heading1'],
        fontName='Helvetica-Bold',
        fontSize=20,
        leading=24,
        textColor=colors.HexColor('#0F172A'), # Slate 900
        spaceAfter=15
    )
    
    section_style = ParagraphStyle(
        'DocSection',
        parent=styles['Heading2'],
        fontName='Helvetica-Bold',
        fontSize=14,
        leading=18,
        textColor=colors.HexColor('#1E3A8A'), # Navy/Blue 900
        spaceBefore=10,
        spaceAfter=8
    )

    body_style = ParagraphStyle(
        'DocBody',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=10,
        leading=14,
        textColor=colors.HexColor('#334155') # Slate 700
    )

    header_style = ParagraphStyle(
        'DocHeader',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=10,
        leading=14,
        textColor=colors.white
    )

    # Document Header
    story.append(Paragraph("GOVERNMENT OF ANDHRA PRADESH", ParagraphStyle('GovHeader', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=8, leading=10, textColor=colors.HexColor('#475569'), alignment=1)))
    story.append(Paragraph("DEPARTMENT OF MINES & GEOLOGY - GRANITE INSPECTION REPORT", ParagraphStyle('GovSubHeader', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=10, leading=12, textColor=colors.HexColor('#0F172A'), alignment=1, spaceAfter=15)))
    
    # Title
    story.append(Paragraph(f"Granite Block Report: {block.block_id}", title_style))
    story.append(Spacer(1, 5))
    
    # Metadata table
    meta_data = [
        [Paragraph("Field", header_style), Paragraph("Inspection Details", header_style)],
        [Paragraph("Block Number / ID", body_style), Paragraph(block.block_id or "N/A", body_style)],
        [Paragraph("Quarry Reference ID", body_style), Paragraph(str(block.quarry.name) if block.quarry else "Unspecified Quarry", body_style)],
        [Paragraph("Status / Stage", body_style), Paragraph(block.status.upper() if block.status else "PENDING", body_style)],
        [Paragraph("GPS Coordinates", body_style), Paragraph(f"Lat: {block.gps_latitude or 'N/A'}, Lon: {block.gps_longitude or 'N/A'}", body_style)],
        [Paragraph("Capture Timestamp", body_style), Paragraph(block.captured_at.strftime('%Y-%m-%d %H:%M:%S') if block.captured_at else "N/A", body_style)],
        [Paragraph("Approval Status", body_style), Paragraph(block.approval_status.upper() if block.approval_status else "PENDING", body_style)]
    ]
    
    if block.approval_status in ['approved', 'rejected']:
        meta_data.append([Paragraph("Action Officer", body_style), Paragraph(block.approved_by or "System", body_style)])
        meta_data.append([Paragraph("Action Time", body_style), Paragraph(block.approved_at.strftime('%Y-%m-%d %H:%M:%S') if block.approved_at else "N/A", body_style)])
        
    t_meta = Table(meta_data, colWidths=[150, 390])
    t_meta.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1E3A8A')),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('BOTTOMPADDING', (0,0), (-1,0), 6),
        ('TOPPADDING', (0,0), (-1,0), 6),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#CBD5E1')),
        ('BACKGROUND', (0,1), (0,-1), colors.HexColor('#F8FAFC')),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('ROWBACKGROUNDS', (1,1), (-1,-1), [colors.white, colors.HexColor('#F8FAFC')])
    ]))
    story.append(t_meta)
    story.append(Spacer(1, 15))
    
    # Measurements table
    story.append(Paragraph("Geometric Dimensions", section_style))
        
    meas = block.measurement
    meas_data = [
        [Paragraph("Metric", header_style), Paragraph("Dimension Value", header_style)],
        [Paragraph("Length (m)", body_style), Paragraph(f"{meas.length_m:.2f} m" if meas else "N/A", body_style)],
        [Paragraph("Breadth (m)", body_style), Paragraph(f"{meas.breadth_m:.2f} m" if meas else "N/A", body_style)],
        [Paragraph("Height (m)", body_style), Paragraph(f"{meas.height_m:.2f} m" if meas else "N/A", body_style)],
        [Paragraph("Volume (m3)", body_style), Paragraph(f"{meas.volume_m3:.4f} m³" if meas else "N/A", body_style)],
        [Paragraph("Confidence Score", body_style), Paragraph(f"{meas.confidence:.2f}" if meas else "N/A", body_style)]
    ]
    
    t_meas = Table(meas_data, colWidths=[150, 390])
    t_meas.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#475569')),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('BOTTOMPADDING', (0,0), (-1,0), 6),
        ('TOPPADDING', (0,0), (-1,0), 6),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#CBD5E1')),
        ('BACKGROUND', (0,1), (0,-1), colors.HexColor('#F8FAFC')),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('ROWBACKGROUNDS', (1,1), (-1,-1), [colors.white, colors.HexColor('#F8FAFC')])
    ]))
    story.append(t_meas)
    story.append(Spacer(1, 15))
    
    # Original CV parameters snapshot if overridden
    if block.is_overridden and block.original_measurement:
        story.append(Paragraph("Audit Trail: Original CV Measurement Snapshot", ParagraphStyle('AuditTitle', parent=section_style, textColor=colors.HexColor('#B91C1C'))))
        orig_meas = block.original_measurement
        orig_data = [
            [Paragraph("Metric", header_style), Paragraph("Original CV Value", header_style)],
            [Paragraph("Length (m)", body_style), Paragraph(f"{orig_meas.length_m:.2f} m", body_style)],
            [Paragraph("Breadth (m)", body_style), Paragraph(f"{orig_meas.breadth_m:.2f} m", body_style)],
            [Paragraph("Height (m)", body_style), Paragraph(f"{orig_meas.height_m:.2f} m", body_style)],
            [Paragraph("Volume (m3)", body_style), Paragraph(f"{orig_meas.volume_m3:.4f} m³", body_style)],
            [Paragraph("CV Confidence", body_style), Paragraph(f"{orig_meas.confidence:.2f}", body_style)]
        ]
        t_orig = Table(orig_data, colWidths=[150, 390])
        t_orig.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#B91C1C')),
            ('ALIGN', (0,0), (-1,-1), 'LEFT'),
            ('BOTTOMPADDING', (0,0), (-1,0), 6),
            ('TOPPADDING', (0,0), (-1,0), 6),
            ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#CBD5E1')),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#FEF2F2')])
        ]))
        story.append(t_orig)
        story.append(Spacer(1, 15))
        
    # Assessment information
    if assessment:
        story.append(Paragraph("Seigniorage Assessment Details (POC Rates)", section_style))
        ass_data = [
            [Paragraph("Parameter", header_style), Paragraph("Value / Calculated Fee", header_style)],
            [Paragraph("Granite Category", body_style), Paragraph(assessment.granite_category or "N/A", body_style)],
            [Paragraph("Gangsaw Classification", body_style), Paragraph(assessment.gangsaw_classification or "N/A", body_style)],
            [Paragraph("Estimated Weight (MT)", body_style), Paragraph(f"{assessment.weight_mt:.3f} MT", body_style)],
            [Paragraph("Density Snapshot (MT/m³)", body_style), Paragraph(f"{assessment.density_mt_per_m3:.2f}", body_style)],
            [Paragraph("POC Rate per MT", body_style), Paragraph(f"INR {assessment.rate_per_mt:,.2f}", body_style)],
            [Paragraph("Indicative Seigniorage Fee", body_style), Paragraph(f"INR {assessment.indicative_seigniorage:,.2f}", body_style)]
        ]
        t_ass = Table(ass_data, colWidths=[200, 340])
        t_ass.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1E3A8A')),
            ('ALIGN', (0,0), (-1,-1), 'LEFT'),
            ('BOTTOMPADDING', (0,0), (-1,0), 6),
            ('TOPPADDING', (0,0), (-1,0), 6),
            ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#CBD5E1')),
            ('BACKGROUND', (0,1), (0,-1), colors.HexColor('#F8FAFC')),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('ROWBACKGROUNDS', (1,1), (-1,-1), [colors.white, colors.HexColor('#F8FAFC')])
        ]))
        story.append(t_ass)
        story.append(Spacer(1, 15))
        
    # Images Section
    story.append(Paragraph("Visual Evidence (Annotated Output)", section_style))
    image_rendered = False
    
    if block.annotated_image_path:
        img_full_path = os.path.join(settings.MEDIA_ROOT, block.annotated_image_path)
        if os.path.exists(img_full_path):
            try:
                # 400x300 image sizing
                report_img = Image(img_full_path, width=400, height=300)
                story.append(report_img)
                image_rendered = True
            except Exception as e:
                story.append(Paragraph(f"[Could not load annotated image: {e}]", body_style))
                
    if not image_rendered and block.raw_image_path:
        img_full_path = os.path.join(settings.MEDIA_ROOT, block.raw_image_path)
        if os.path.exists(img_full_path):
            try:
                report_img = Image(img_full_path, width=400, height=300)
                story.append(report_img)
                image_rendered = True
            except Exception as e:
                story.append(Paragraph(f"[Could not load raw image: {e}]", body_style))
                
    if not image_rendered:
        story.append(Paragraph("[No CV annotated image or raw image file available on disk]", body_style))
        
    doc.build(story)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes
