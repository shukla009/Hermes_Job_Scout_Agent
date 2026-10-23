"""Explicit visible destinations remain hints until public vacancy verification."""
from scout_mail import alert_destinations


def test_html_visible_official_url_is_found_before_opaque_tracker():
    target = 'https://careers.example/jobs/42'
    html = '<a href="https://email.linkedin.com/t/opaque">' + target + '</a>'
    links = alert_destinations('text/html', html)
    assert links[0][0] == target
    assert ('https://email.linkedin.com/t/opaque', target) in links


def test_bare_visible_employer_url_is_a_hint_without_invented_date():
    links = alert_destinations('text/html', '<p>Posted today: https://careers.example/positions/42.</p>')
    assert links == [('https://careers.example/positions/42', 'Employer vacancy link')]


def test_hidden_unsafe_and_non_vacancy_urls_are_not_added():
    html = ('<script>https://careers.example/jobs/hidden</script>'
            '<style>https://careers.example/jobs/style</style>'
            '<p>https://127.0.0.1/jobs/private https://www.naukri.com/jobs/42 '
            'https://www.linkedin.com/jobs/view/42 https://careers.example/privacy</p>')
    assert alert_destinations('text/html', html) == []


def test_duplicate_visible_and_href_destinations_are_deduplicated():
    target = 'https://careers.example/jobs/42'
    assert len(alert_destinations('text/html', f'<a href="{target}">{target}</a>')) == 1
