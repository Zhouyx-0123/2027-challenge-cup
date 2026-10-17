from src.preprocess import build_wide_table
from src.optimizer import calculate_carbon
from src.evaluator import evaluate_schemes

def run_all():
    print(" 流水线启动 ")
    wide_df = build_wide_table()
    carbon_df = calculate_carbon(wide_df)
    evaluate_schemes(carbon_df)
    print("流水线全部跑通！")

if __name__ == "__main__":
    run_all()