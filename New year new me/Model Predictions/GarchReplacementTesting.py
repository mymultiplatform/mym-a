import torch
import pandas as pd
import numpy as np
from pathlib import Path
from dataclasses import dataclass
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import multiprocessing

@dataclass
class Config:
    def __init__(self):
        self.BASE_DIR = r'C:\Users\cinco\Desktop\DATA FOR SCRIPTS'
        self.DATA_DIR = r'C:\Users\cinco\Desktop\DATA FOR SCRIPTS\data bento data\finaltest'  # Updated to match your processed data path
        self.MODEL_PATH = r'C:\Users\cinco\Desktop\DATA FOR SCRIPTS\Paths\12.31\best_model.pth'  # Updated to match your model path
        self.TEST_START = pd.to_datetime('2024-09-16T08:00:04.248723179Z').tz_convert('UTC')
        
        # Model configurations
        self.INPUT_SIZE = 9  # Updated to match your 10 features
        self.HIDDEN_SIZE = 256
        self.NUM_LAYERS = 2
        self.OUTPUT_SIZE = 1
        self.SEQUENCE_LENGTH = 30
        self.CHUNK_SIZE = 100000
        
        # Performance optimizations
        self.PIN_MEMORY = True
        self.PREFETCH_FACTOR = 2
        self.BATCH_SIZE = 4096
        self.CUDA_ALLOC_CONF = 'max_split_size_mb:128,expandable_segments:True'
        self.NUM_WORKERS = max(multiprocessing.cpu_count() - 1, 1)
        
        # Updated dtype dictionary to match your processed data
        self.DTYPE_DICT = {
            'n_delta': 'float32',
            'log_normalized_returns': 'float32',
            'returns_squared_log_normalized': 'float32',
            'rolling_vol_5_log_normalized': 'float32',
            'rolling_vol_15_log_normalized': 'float32',
            'rolling_vol_30_log_normalized': 'float32',
            'rolling_mean_5_log_normalized': 'float32',
            'rolling_mean_15_log_normalized': 'float32',
            'rolling_mean_30_log_normalized': 'float32'
        }
class GARCHLikeModel_New(torch.nn.Module):
    def __init__(self, input_size=14, hidden_size=256, num_layers=2):  # Updated input_size
        super().__init__()
        self.lstm = torch.nn.LSTM(input_size, hidden_size, num_layers, 
                                batch_first=True, bidirectional=True)
        self.dropout = torch.nn.Dropout(0.2)
        self.mean_head = torch.nn.Sequential(
            torch.nn.Linear(hidden_size * 2, 128),  # Increased intermediate layer size
            torch.nn.ReLU(),
            torch.nn.Linear(128, 1)
        )
        self.variance_head = torch.nn.Sequential(
            torch.nn.Linear(hidden_size * 2, 128),  # Increased intermediate layer size
            torch.nn.ReLU(),
            torch.nn.Linear(128, 1),
            torch.nn.Softplus()
        )

    def forward(self, x):
        lstm_out, _ = self.lstm(x)
        last_hidden = lstm_out[:, -1]
        features = self.dropout(last_hidden)
        
        mean = self.mean_head(features)
        variance = self.variance_head(features)
        
        return mean, variance
class LSTMModel(torch.nn.Module):
    def __init__(self, input_size=2, hidden_size=512, num_layers=6):
        super().__init__()
        self.lstm = torch.nn.LSTM(input_size, hidden_size, num_layers, 
                                batch_first=True, bidirectional=True)
        self.dropout = torch.nn.Dropout(0.3)
        self.fc1 = torch.nn.Linear(hidden_size * 2, hidden_size * 2)
        self.fc2 = torch.nn.Linear(hidden_size * 2, 128)
        self.fc3 = torch.nn.Linear(128, 5)
        self.relu = torch.nn.ReLU()

    def forward(self, x):
        lstm_out, _ = self.lstm(x)
        last_hidden = lstm_out[:, -1]
        out = self.dropout(last_hidden)
        out = self.relu(self.fc1(out))
        out = self.relu(self.fc2(out))
        return self.fc3(out)

def load_test_data(config):
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    print(f"Using device: {device}")

    dfs = []
    files = sorted(Path(config.DATA_DIR).glob('processed_*.csv'))  # Updated to match your processed file prefix
    
    with ThreadPoolExecutor(max_workers=4) as executor:
        for f in tqdm(files, desc="Processing CSV files"):
            df = pd.read_csv(
                f,
                dtype=config.DTYPE_DICT,
                parse_dates=['ts_event'],
                usecols=['ts_event'] + list(config.DTYPE_DICT.keys()),
                low_memory=False,
                engine='c'
            )
            dfs.append(df)

    final_data = pd.concat(dfs, ignore_index=True)
    final_data['ts_event'] = pd.to_datetime(final_data['ts_event'])
    filtered_data = final_data[final_data['ts_event'] >= config.TEST_START]
    
    return filtered_data.sort_values('ts_event')


def prepare_sequence(data, sequence_length=30):
    feature_columns = [
        'n_delta',
        'log_normalized_returns',
        'returns_squared_log_normalized',
        'rolling_vol_5_log_normalized',
        'rolling_vol_15_log_normalized',
        'rolling_vol_30_log_normalized',
        'rolling_mean_5_log_normalized',
        'rolling_mean_15_log_normalized',
        'rolling_mean_30_log_normalized'
    ]
    features = data[feature_columns].values
    return torch.tensor(features, dtype=torch.float32).unsqueeze(0)

def predict(model, sequences):
    model.eval()
    with torch.no_grad():
        return model(sequences).cpu().numpy()
def rolling_forecast(model, data, window_size=30, horizon=5):
    model.eval()
    forecasts = []
    
    with torch.no_grad():
        for i in range(0, len(data) - window_size - horizon + 1):
            # Get window of data
            window = data[i:i+window_size]
            
            # Prepare sequence
            sequence = prepare_sequence(window)
            sequence = sequence.cuda()
            
            # Get predictions
            mean, variance = model(sequence)
            
            # Store results
            forecasts.append({
                'timestamp': data['ts_event'].iloc[i+window_size],
                'predicted_mean': mean.cpu().numpy()[0][0],
                'predicted_variance': variance.cpu().numpy()[0][0]
            })
            
    return pd.DataFrame(forecasts)


def plot_results_with_intervals(df):
    plt.figure(figsize=(15, 7))
    
    # Plot mean prediction
    plt.plot(df['timestamp'], df['predicted_mean'], 
            label='Predicted Mean', color='blue')
    
    # Plot confidence intervals
    plt.fill_between(df['timestamp'], 
                    df['lower_bound'], 
                    df['upper_bound'],
                    color='blue', alpha=0.2, 
                    label='95% Confidence Interval')
    
    plt.title('Returns Forecast with Volatility Intervals')
    plt.xlabel('Time')
    plt.ylabel('Returns')
    plt.legend()
    
    plt.gcf().autofmt_xdate()
    plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d %H:%M'))
    
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    
    plt.savefig(r'C:\Users\cinco\Desktop\DATA FOR SCRIPTS\garch_like_forecast.png')
    plt.close()
def main():
    config = Config()
    
    # Load old model first
    old_model = LSTMModel(
        input_size=config.INPUT_SIZE,
        hidden_size=config.HIDDEN_SIZE,
        num_layers=config.NUM_LAYERS
    )
    checkpoint = torch.load(config.MODEL_PATH, weights_only=True)    
    old_model.load_state_dict(checkpoint['model_state_dict'])
    
    # Initialize new model
    new_model = GARCHLikeModel_New(
        input_size=config.INPUT_SIZE,
        hidden_size=config.HIDDEN_SIZE,
        num_layers=config.NUM_LAYERS
    )
    
    # Transfer LSTM weights
    new_model.lstm.load_state_dict(old_model.lstm.state_dict())
    
    # Move to GPU
    new_model.cuda()
    
    # Continue with your existing code...
    test_data = load_test_data(config)
    
    # Generate rolling forecasts using new_model instead of model
    forecasts_df = rolling_forecast(
        model=new_model,
        data=test_data,
        window_size=config.SEQUENCE_LENGTH,
        horizon=5
    )
    
    # Calculate confidence intervals
    forecasts_df['upper_bound'] = (forecasts_df['predicted_mean'] + 
                                 1.96 * np.sqrt(forecasts_df['predicted_variance']))
    forecasts_df['lower_bound'] = (forecasts_df['predicted_mean'] - 
                                 1.96 * np.sqrt(forecasts_df['predicted_variance']))
    
    # Save results
    forecasts_df.to_csv(r'C:\Users\cinco\Desktop\DATA FOR SCRIPTS\garch_like_forecasts.csv', 
                       index=False)
    
    # Plot results with confidence intervals
    plot_results_with_intervals(forecasts_df)
if __name__ == "__main__":
   main()