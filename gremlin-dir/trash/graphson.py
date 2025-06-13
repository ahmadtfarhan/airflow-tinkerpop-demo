# # make_graphson.py
# import pandas as pd, json

# nodes   = pd.read_csv('nodes.csv')           # [node,type]
# edges   = pd.read_csv('edges.csv')           # [hero,comic]
# network = pd.read_csv('hero-network.csv')    # [hero1,hero2]

# data = {
#     'vertices': [],
#     'edges': []
# }

# # 1) vertices
# for _, r in nodes.iterrows():
#     data['vertices'].append({
#         'id': r['node'],
#         'label': r['type'],
#         'properties': {'name': [ { 'id': r['node'], 'value': r['node'] } ]}
#     })

# # 2) hero→comic
# for _, r in edges.iterrows():
#     data['edges'].append({
#         'id': f"{r['hero']}→{r['comic']}",
#         'outV': r['hero'], 'inV': r['comic'],
#         'label': 'appears_in'
#     })

# # 3) hero–hero
# for _, r in network.iterrows():
#     data['edges'].append({
#         'id': f"{r['hero1']}→{r['hero2']}",
#         'outV': r['hero1'], 'inV': r['hero2'],
#         'label': 'interacts'
#     })

# print(f"✅ Created json file")
# with open('superhero.json','w',encoding='utf-8') as f:
#     json.dump(data, f)



from gremlin_python.driver.client import Client

client = Client('ws://localhost:8182/gremlin','g')
# this reads the file *server-side* and loads all vertices+edges in one go
client.submit("g.io('/Users/AhmadFarhan/Desktop/repos/new_gremlin/superhero.json').read().iterate()").all().result()
client.close()
print("✅ Bulk loaded GraphSON via Client")

