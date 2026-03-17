use reqwest::Client;
use serde::{Deserialize, Serialize};
use std::env;
use std::net::SocketAddr;
use tokio::net::TcpListener;
use tokio::io::{AsyncReadExt, AsyncWriteExt};

#[derive(Deserialize, Debug)]
struct PointsResponse {
    properties: PointsProperties,
}

#[derive(Deserialize, Debug)]
struct PointsProperties {
    forecast: String,
    #[serde(rename = "forecastHourly")]
    forecast_hourly: String,
    #[serde(rename = "observationStations")]
    observation_stations: String,
}

#[derive(Deserialize, Debug)]
struct ForecastResponse {
    properties: ForecastProperties,
}

#[derive(Deserialize, Debug)]
struct ForecastProperties {
    periods: Vec<ForecastPeriod>,
}

#[derive(Deserialize, Debug, Clone)]
struct ForecastPeriod {
    name: String,
    temperature: f64,
    #[serde(rename = "temperatureUnit")]
    temperature_unit: String,
    #[serde(rename = "windSpeed")]
    wind_speed: String,
    #[serde(rename = "windDirection")]
    wind_direction: String,
    #[serde(rename = "shortForecast")]
    short_forecast: String,
    #[serde(rename = "detailedForecast")]
    detailed_forecast: String,
    #[serde(rename = "isDaytime")]
    is_daytime: bool,
    #[serde(rename = "probabilityOfPrecipitation")]
    probability_of_precipitation: Option<PrecipValue>,
}

#[derive(Deserialize, Debug, Clone)]
struct PrecipValue {
    value: Option<f64>,
}

#[derive(Deserialize, Debug)]
struct StationsResponse {
    features: Vec<StationFeature>,
}

#[derive(Deserialize, Debug)]
struct StationFeature {
    properties: StationProperties,
}

#[derive(Deserialize, Debug)]
struct StationProperties {
    #[serde(rename = "stationIdentifier")]
    station_identifier: String,
}

#[derive(Deserialize, Debug)]
struct ObservationResponse {
    properties: ObservationProperties,
}

#[derive(Deserialize, Debug)]
struct ObservationProperties {
    temperature: MeasurementValue,
    #[serde(rename = "relativeHumidity")]
    relative_humidity: MeasurementValue,
    #[serde(rename = "windSpeed")]
    wind_speed: MeasurementValue,
    #[serde(rename = "windDirection")]
    wind_direction: MeasurementValue,
    #[serde(rename = "heatIndex")]
    heat_index: MeasurementValue,
    #[serde(rename = "textDescription")]
    text_description: String,
}

#[derive(Deserialize, Debug)]
struct MeasurementValue {
    value: Option<f64>,
}

#[derive(Deserialize, Debug)]
struct AlertsResponse {
    features: Vec<AlertFeature>,
}

#[derive(Deserialize, Debug)]
struct AlertFeature {
    properties: AlertProperties,
}

#[derive(Deserialize, Debug)]
struct AlertProperties {
    headline: Option<String>,
}

#[derive(Serialize, Debug, Clone)]
struct WeatherData {
    current: CurrentConditions,
    hourly: Vec<HourlyPoint>,
    daily: Vec<DailyForecast>,
    alerts: Vec<String>,
    last_updated: String,
}

#[derive(Serialize, Debug, Clone)]
struct CurrentConditions {
    temp_f: f64,
    feels_like_f: f64,
    humidity_pct: f64,
    wind_mph: f64,
    wind_dir: String,
    condition: String,
    heat_warning: bool,
}

#[derive(Serialize, Debug, Clone)]
struct HourlyPoint {
    hour: String,
    temp_f: f64,
    condition: String,
    precip_pct: f64,
}

#[derive(Serialize, Debug, Clone)]
struct DailyForecast {
    day: String,
    high_f: f64,
    low_f: f64,
    precip_pct: f64,
    summary: String,
    is_rainy: bool,
}

fn celsius_to_f(c: f64) -> f64 {
    (c * 9.0 / 5.0) + 32.0
}

fn ms_to_mph(ms: f64) -> f64 {
    ms * 2.237
}

fn wind_deg_to_cardinal(deg: f64) -> String {
    let dirs = ["N","NNE","NE","ENE","E","ESE","SE","SSE","S","SSW","SW","WSW","W","WNW","NW","NNW"];
    let index = ((deg / 22.5) + 0.5) as usize % 16;
    dirs[index].to_string()
}

async fn fetch_weather(client: &Client) -> Result<WeatherData, Box<dyn std::error::Error>> {
    let lat = 31.3271_f64;
    let lon = -89.2903_f64;

    println!("[weather] Fetching grid metadata for Hattiesburg...");
    let points_url = format!("https://api.weather.gov/points/{},{}", lat, lon);
    let points: PointsResponse = client
        .get(&points_url)
        .header("User-Agent", "HattiesburgWeatherApp/1.0 (contact@example.com)")
        .send().await?
        .json().await?;

    println!("[weather] Fetching daily forecast...");
    let forecast: ForecastResponse = client
        .get(&points.properties.forecast)
        .header("User-Agent", "HattiesburgWeatherApp/1.0 (contact@example.com)")
        .send().await?
        .json().await?;

    println!("[weather] Fetching hourly forecast...");
    let hourly_forecast: ForecastResponse = client
        .get(&points.properties.forecast_hourly)
        .header("User-Agent", "HattiesburgWeatherApp/1.0 (contact@example.com)")
        .send().await?
        .json().await?;

    println!("[weather] Fetching observation stations...");
    let stations: StationsResponse = client
        .get(&points.properties.observation_stations)
        .header("User-Agent", "HattiesburgWeatherApp/1.0 (contact@example.com)")
        .send().await?
        .json().await?;

    let station_id = stations.features
        .first()
        .map(|s| s.properties.station_identifier.clone())
        .unwrap_or_default();

    println!("[weather] Fetching latest observation from {}...", station_id);
    let obs_url = format!("https://api.weather.gov/stations/{}/observations/latest", station_id);
    let obs: ObservationResponse = client
        .get(&obs_url)
        .header("User-Agent", "HattiesburgWeatherApp/1.0 (contact@example.com)")
        .send().await?
        .json().await?;

    println!("[weather] Fetching active alerts for Mississippi...");
    let alerts_resp: AlertsResponse = client
        .get("https://api.weather.gov/alerts/active?area=MS")
        .header("User-Agent", "HattiesburgWeatherApp/1.0 (contact@example.com)")
        .send().await?
        .json().await?;

    let temp_c = obs.properties.temperature.value.unwrap_or(27.0);
    let temp_f = celsius_to_f(temp_c);
    let heat_index_c = obs.properties.heat_index.value;
    let feels_like_f = match heat_index_c {
        Some(hi) if hi > temp_c => celsius_to_f(hi),
        _ => temp_f,
    };
    let humidity = obs.properties.relative_humidity.value.unwrap_or(70.0);
    let wind_ms = obs.properties.wind_speed.value.unwrap_or(0.0);
    let wind_mph = ms_to_mph(wind_ms);
    let wind_deg = obs.properties.wind_direction.value.unwrap_or(0.0);
    let wind_dir = wind_deg_to_cardinal(wind_deg);
    let condition = obs.properties.text_description.clone();
    let heat_warning = feels_like_f >= 100.0;

    let current = CurrentConditions {
        temp_f: (temp_f * 10.0).round() / 10.0,
        feels_like_f: (feels_like_f * 10.0).round() / 10.0,
        humidity_pct: humidity.round(),
        wind_mph: (wind_mph * 10.0).round() / 10.0,
        wind_dir,
        condition,
        heat_warning,
    };

    let hourly: Vec<HourlyPoint> = hourly_forecast.properties.periods
        .iter()
        .take(12)
        .map(|p| {
            let precip = p.probability_of_precipitation
                .as_ref()
                .and_then(|v| v.value)
                .unwrap_or(0.0);
            HourlyPoint {
                hour: p.name.clone(),
                temp_f: p.temperature,
                condition: p.short_forecast.clone(),
                precip_pct: precip,
            }
        })
        .collect();

    let periods = &forecast.properties.periods;
    let mut daily: Vec<DailyForecast> = Vec::new();
    let mut i = 0;
    while i < periods.len() && daily.len() < 7 {
        let day_period = &periods[i];
        if day_period.is_daytime {
            let night_period = periods.get(i + 1);
            let low_f = night_period.map(|n| n.temperature).unwrap_or(day_period.temperature - 15.0);
            let precip = day_period.probability_of_precipitation
                .as_ref()
                .and_then(|v| v.value)
                .unwrap_or(0.0);
            let summary = day_period.short_forecast.clone();
            let is_rainy = summary.to_lowercase().contains("rain")
                || summary.to_lowercase().contains("shower")
                || summary.to_lowercase().contains("storm")
                || precip >= 40.0;
            daily.push(DailyForecast {
                day: day_period.name.clone(),
                high_f: day_period.temperature,
                low_f,
                precip_pct: precip,
                summary,
                is_rainy,
            });
            i += 2;
        } else {
            i += 1;
        }
    }

    let alerts: Vec<String> = alerts_resp.features
        .iter()
        .filter_map(|f| f.properties.headline.clone())
        .take(3)
        .collect();

    let now = chrono::Local::now();
    let last_updated = now.format("%b %d, %Y %I:%M %p").to_string();

    Ok(WeatherData { current, hourly, daily, alerts, last_updated })
}

async fn run_server(data: WeatherData) {
    let addr: SocketAddr = "127.0.0.1:8765".parse().unwrap();
    let listener = TcpListener::bind(addr).await.unwrap();
    println!("[weather] Server listening on http://{}", addr);

    let json = serde_json::to_string(&data).unwrap();
    let json_bytes = json.into_bytes();

    loop {
        if let Ok((mut stream, _)) = listener.accept().await {
            let response_body = json_bytes.clone();
            tokio::spawn(async move {
                let mut buf = [0u8; 1024];
                let _ = stream.read(&mut buf).await;
                let response = format!(
                    "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nAccess-Control-Allow-Origin: *\r\nContent-Length: {}\r\n\r\n",
                    response_body.len()
                );
                let _ = stream.write_all(response.as_bytes()).await;
                let _ = stream.write_all(&response_body).await;
            });
        }
    }
}


#[tokio::main]
async fn main() {
    let args: Vec<String> = env::args().collect();
    let serve_mode = args.contains(&"--serve".to_string());
    let output_path = env::temp_dir().join("hattiesburg_weather.json");

    let client = Client::builder()
        .timeout(std::time::Duration::from_secs(30))
        .build()
        .expect("Failed to create HTTP client");

    println!("[weather] Fetching Hattiesburg weather data...");
    match fetch_weather(&client).await {
        Ok(data) => {
            let json = serde_json::to_string_pretty(&data).unwrap();
            std::fs::write(&output_path, &json).expect("Failed to write weather JSON");
            println!("[weather] Data written to {}", output_path.display());
            if serve_mode {
                run_server(data).await;
            } else {
                println!("{}", json);
            }
        }
        Err(e) => {
            eprintln!("[weather] Error fetching data: {}", e);
            std::process::exit(1);
        }
    }
}