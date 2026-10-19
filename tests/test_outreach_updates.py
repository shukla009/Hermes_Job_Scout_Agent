from scout_mail import recruitment_contacts
import scout_agent


def test_recruitment_contact_on_next_line():
    assert recruitment_contacts({'page_fetched':True,'source_text':
        'Junior Analyst hiring\nSend your resume to:\ncareers@company.example\n'}) == ['careers@company.example']


def test_distant_hiring_header_does_not_authorise_footer():
    assert recruitment_contacts({'page_fetched':True,'source_text':
        'Send your resume through our portal\n\nAbout our company\nPrivacy contact\nhr@company.example'}) == []


def test_email_reflects_current_profile_and_real_vacancy():
    result, _ = scout_agent.compose_email({'page_fetched':True, 'description':'Conduct market research. Required skills: Excel and SQL.'}, {'job_title':'Research Analyst', 'company':'Example',
        'family':'research','matched_skills':['Excel','SQL'],
        'application_url':'https://example.com/jobs/123'})
    body = result['email_body']
    assert 'BSc Data Analytics graduate' in body
    assert 'currently working in research and strategy at ProSca' in body
    assert 'https://example.com/jobs/123' in body
    assert 'Relevant skills: Research, Excel, SQL (knowledge).' in body
    assert 'LPA' not in body and 'immediate join' not in body.lower()
