import pandas as pd
df = pd.read_excel('GIS_23_fixed_output_v7.xlsx')
total = df['CURRENT_PREMIUM'].fillna(0).sum()
print(f"Final Total (GIS 23 v7): ${total:,.2f}")
print(f"Total Rows: {len(df)}")
print(f"Unique Names: {len(df.groupby(['LASTNAME', 'FIRSTNAME']))}")
