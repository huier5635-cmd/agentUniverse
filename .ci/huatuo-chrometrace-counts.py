import json,sys
events=[json.loads(line) for line in open(sys.argv[1],encoding='utf-8') if line.strip()]
results={(x['Package'],x['Test']):x['Action'] for x in events if x.get('Test') and x['Action'] in ['pass','fail','skip']}
leaves={k:v for k,v in results.items() if not any(p==k[0] and n.startswith(k[1]+'/') for p,n in results)}
counts={action:sum(v==action for v in leaves.values()) for action in ['pass','fail','skip']}
print(json.dumps({'leafCounts':counts,'outcomes':{k[1]:v for k,v in leaves.items()}}))
assert counts=={'pass':int(sys.argv[3]),'fail':int(sys.argv[2]),'skip':0},counts
