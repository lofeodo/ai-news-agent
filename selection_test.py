"""Quick selection test — runs full select_articles_for_category against local news_summaries.json."""
import sys, json, os
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, '.')

import anthropic
from agents.agent3_compose import select_articles_for_category

client = anthropic.Anthropic(api_key=os.environ['ANTHROPIC_1ST_API_KEY'])
prompt_template = open('prompts/article_selection_prompt.txt', encoding='utf-8').read()

with open('data/news_summaries.json', encoding='utf-8') as f:
    d = json.load(f)

by_cat = {}
for a in d['articles']:
    by_cat.setdefault(a['category'], []).append(a)

for cat, arts in sorted(by_cat.items()):
    selected = select_articles_for_category(cat, arts, prompt_template, client)
    selected_titles = {a['title'] for a in selected}

    # Sorted pool (same as agent3) for display
    sorted_arts = sorted(arts, key=lambda a: a.get('hn_score') or -1, reverse=True)

    print(f'\n=== {cat} ({len(arts)} articles) ===')
    for a in selected:
        hn = a.get('hn_score')
        print(f'  SELECTED  HN={str(hn) if hn is not None else "null":>6}  {a["title"]}')
    print(f'  --- top skipped ---')
    for a in sorted_arts[:6]:
        if a['title'] not in selected_titles:
            hn = a.get('hn_score')
            print(f'  skipped   HN={str(hn) if hn is not None else "null":>6}  {a["title"]}')
