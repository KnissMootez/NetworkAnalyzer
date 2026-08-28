import pandas as pd

splits = {'train': 'train.csv', 'validation': 'validation.csv', 'test': 'test.csv'}

for split_name, filename in splits.items():
    df = pd.read_csv("hf://datasets/aai510-group1/telco-customer-churn/" + filename)
    df.to_csv(f"local_{filename}", index=False)  # Save locally without index
    print(f"Saved {filename} to local_{filename}")