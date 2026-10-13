from scout_matching import evaluate_job


def vacancy(title, description):
    return {'title': title, 'source_text':description, 'location':'Noida',
            'employment_type':'Full Time', 'active_listing':True}


def test_market_intelligence_title_cannot_hide_field_sales():
    result = evaluate_job(vacancy('Associate Market Intelligence',
        'Perform research and insights. Drive on-ground user acquisition, visit merchants and achieve sales targets.'))
    assert not result['eligible']
    assert 'Sales/acquisition' in result['reason']


def test_dashboard_analyst_alias():
    result = evaluate_job(vacancy('Dashboard Analyst',
        'Build Power BI dashboards, Excel reports and reporting insights. Entry level opportunity.'))
    assert result['eligible'] and result['family'] == 'reporting'


def test_data_validation_analyst_alias():
    result = evaluate_job(vacancy('Data Validation Analyst',
        'Perform data cleaning, data validation and data quality analysis using Excel. Entry level.'))
    assert result['eligible'] and result['family'] == 'data'


def test_mandatory_bi_administration_is_not_dashboard_experience():
    result = evaluate_job(vacancy('Power BI Analyst',
        'Create Power BI dashboards and reports. Must have server installation and cloud administration experience.'))
    assert not result['eligible']
