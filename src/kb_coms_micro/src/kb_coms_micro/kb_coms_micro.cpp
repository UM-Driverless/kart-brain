#include "kb_coms_micro/kb_coms_micro.hpp"

KB_coms_micro::KB_coms_micro() : Node("kb_coms_micro_node") {

    // Declare parameters with defaults
    this->declare_parameter<std::string>("serial_port", "/dev/serial/by-id/usb-1a86_USB_Single_Serial_5C37207028-if00");
    this->declare_parameter<int>("baudrate", 115200);

    // Get parameters
    std::string port;
    int baud;
    this->get_parameter("serial_port", port);
    this->get_parameter("baudrate", baud);

    // Create publishers
    esp_heart_pub_ = create_publisher<kb_interfaces::msg::Frame>("/esp32/heartbeat", 10);

    esp_speed_pub_ = create_publisher<kb_interfaces::msg::Frame>("/esp32/speed", 10);

    esp_acceleration_pub_ = create_publisher<kb_interfaces::msg::Frame>("/esp32/acceleration", 10);

    esp_braking_pub_ = create_publisher<kb_interfaces::msg::Frame>("/esp32/braking", 10);

    esp_steering_pub_ = create_publisher<kb_interfaces::msg::Frame>("/esp32/steering", 10);

    esp_mision_pub_ = create_publisher<kb_interfaces::msg::Frame>("/esp32/mision", 10);

    esp_machine_state_pub_ = create_publisher<kb_interfaces::msg::Frame>("/esp32/machine_state", 10);

    esp_shutdown_pub_ = create_publisher<kb_interfaces::msg::Frame>("/esp32/shutdown", 10);

    esp_health_flags_pub_ = create_publisher<kb_interfaces::msg::Frame>("/esp32/health/flags", 10);

    esp_health_data_pub_ = create_publisher<kb_interfaces::msg::Frame>("/esp32/health/data", 10);

    esp_diag_steering_pub_ = create_publisher<kb_interfaces::msg::Frame>("/esp32/diag_steering", 10);

    esp_pneumatic_pub_ = create_publisher<kb_interfaces::msg::Frame>("/esp32/pneumatic", 10);

    esp_steer_pid_pub_ = create_publisher<kb_interfaces::msg::Frame>("/esp32/steer_pid", 10);

    esp_pedals_pub_ = create_publisher<kb_interfaces::msg::Frame>("/esp32/pedals", 10);

    esp_fps_pub_ = create_publisher<std_msgs::msg::Float32>("/esp32/fps", 10);

    esp_safety_pub_ = create_publisher<kb_interfaces::msg::Frame>("/esp32/safety", 10);
    orin_safety_reset_sub_ = create_subscription<kb_interfaces::msg::Frame>(
        "/orin/safety_reset", 10, std::bind(&KB_coms_micro::kb_coms_TXcallback, this, std::placeholders::_1));

    // Create Subscriptors
    orin_throttle_sub_ = create_subscription<kb_interfaces::msg::Frame>(
        "/orin/throttle", 10, std::bind(&KB_coms_micro::kb_coms_TXcallback, this, std::placeholders::_1));

    orin_brake_sub_ = create_subscription<kb_interfaces::msg::Frame>(
        "/orin/brake", 10, std::bind(&KB_coms_micro::kb_coms_TXcallback, this, std::placeholders::_1));
    
    orin_steering_sub_ = create_subscription<kb_interfaces::msg::Frame>(
        "/orin/steering", 10, std::bind(&KB_coms_micro::kb_coms_TXcallback, this, std::placeholders::_1));

    orin_machine_state_sub_ = create_subscription<kb_interfaces::msg::Frame>(
        "/orin/machine_state", 10, std::bind(&KB_coms_micro::kb_coms_TXcallback, this, std::placeholders::_1));

    orin_mision_sub_ = create_subscription<kb_interfaces::msg::Frame>(
        "/orin/mision", 10, std::bind(&KB_coms_micro::kb_coms_TXcallback, this, std::placeholders::_1));

    orin_heartbeat_sub_ = create_subscription<kb_interfaces::msg::Frame>(
        "/orin/heartbeat", 10, std::bind(&KB_coms_micro::kb_coms_TXcallback, this, std::placeholders::_1));
    
    orin_shutdown_sub_ = create_subscription<kb_interfaces::msg::Frame>(
        "/orin/shutdown", 10, std::bind(&KB_coms_micro::kb_coms_TXcallback, this, std::placeholders::_1));

    orin_steer_mode_sub_ = create_subscription<kb_interfaces::msg::Frame>(
        "/orin/steer_mode", 10, std::bind(&KB_coms_micro::kb_coms_TXcallback, this, std::placeholders::_1));

    orin_compressor_disable_sub_ = create_subscription<kb_interfaces::msg::Frame>(
        "/orin/compressor_disable", 10, std::bind(&KB_coms_micro::kb_coms_TXcallback, this, std::placeholders::_1));

    orin_steer_pid_sub_ = create_subscription<kb_interfaces::msg::Frame>(
        "/orin/steer_pid", 10, std::bind(&KB_coms_micro::kb_coms_TXcallback, this, std::placeholders::_1));

    // Inicializa la librería serial
    serial_ = std::make_unique<SerialDriver>(
        port, baud, [this](const SerialDriver::Frame &frame) { this->kb_coms_RXcallback(frame); });

    timer_ = this->create_wall_timer(
        std::chrono::seconds(1),
        std::bind(&KB_coms_micro::kb_coms_OrinHeartbeat, this));

    fps_last_sample_ = std::chrono::steady_clock::now();
    fps_timer_ = this->create_wall_timer(
        std::chrono::seconds(1),
        std::bind(&KB_coms_micro::kb_coms_PublishFps, this));

    serial_->start();
}

void KB_coms_micro::kb_coms_PublishFps(void) {
    // Sample-and-clear the steering frame counter; divide by elapsed wall time
    // (steady_clock) so the rate is correct even if the timer fires late.
    auto now = std::chrono::steady_clock::now();
    double dt = std::chrono::duration<double>(now - fps_last_sample_).count();
    fps_last_sample_ = now;

    uint32_t count = steering_frame_count_.exchange(0);

    std_msgs::msg::Float32 msg;
    msg.data = (dt > 0.0) ? static_cast<float>(count / dt) : 0.0f;
    esp_fps_pub_->publish(msg);
}

KB_coms_micro::~KB_coms_micro() { serial_->stop(); }

void KB_coms_micro::kb_coms_OrinHeartbeat(void) {

    uint8_t type = static_cast<uint8_t>(message_type_t::ORIN_HEARTBEAT);
    std::vector<int32_t> payload;

    serial_->send(type, payload);
}

// Callback de mensajes de la ESP
void KB_coms_micro::kb_coms_RXcallback(const SerialDriver::Frame &frame_esp) {
    RCLCPP_DEBUG(this->get_logger(), "Se ha recibido un msg: %d", frame_esp.type);

    switch(frame_esp.type) {
    case kb_interfaces::msg::Frame::ESP_ACT_SPEED: {
        kb_interfaces::msg::Frame msg_orin1;

        msg_orin1.type = frame_esp.type;
        msg_orin1.payload = frame_esp.payload;

        esp_speed_pub_->publish(msg_orin1);

        break;
    }

    case kb_interfaces::msg::Frame::ESP_ACT_ACCELERATION: {
        kb_interfaces::msg::Frame msg_orin2;

        msg_orin2.type = frame_esp.type;
        msg_orin2.payload = frame_esp.payload;

        esp_acceleration_pub_->publish(msg_orin2);

        break;
    }

    case kb_interfaces::msg::Frame::ESP_ACT_BRAKING: {
        kb_interfaces::msg::Frame msg_orin3;

        msg_orin3.type = frame_esp.type;
        msg_orin3.payload = frame_esp.payload;

        esp_braking_pub_->publish(msg_orin3);

        break;
    }

    case kb_interfaces::msg::Frame::ESP_ACT_STEERING: {
        kb_interfaces::msg::Frame msg_orin4;

        msg_orin4.type = frame_esp.type;
        msg_orin4.payload = frame_esp.payload;

        esp_steering_pub_->publish(msg_orin4);

        // Count for /esp32/fps — steering is the high-rate critical-path frame.
        steering_frame_count_.fetch_add(1, std::memory_order_relaxed);

        break;
    }

    case kb_interfaces::msg::Frame::ESP_MISION: {
        kb_interfaces::msg::Frame msg_orin5;

        msg_orin5.type = frame_esp.type;
        msg_orin5.payload = frame_esp.payload;

        esp_mision_pub_->publish(msg_orin5);

        break;
    }

    case kb_interfaces::msg::Frame::ESP_MACHINE_STATE: {
        kb_interfaces::msg::Frame msg_orin6;

        msg_orin6.type = frame_esp.type;
        msg_orin6.payload = frame_esp.payload;

        esp_machine_state_pub_->publish(msg_orin6);

        break;
    }

    case kb_interfaces::msg::Frame::ESP_ACT_SHUTDOWN: {
        kb_interfaces::msg::Frame msg_orin7;

        msg_orin7.type = frame_esp.type;
        msg_orin7.payload = frame_esp.payload;

        esp_shutdown_pub_->publish(msg_orin7);

        break;
    }

    case kb_interfaces::msg::Frame::ESP_HEARTBEAT: {
        kb_interfaces::msg::Frame msg_orin8;

        msg_orin8.type = frame_esp.type;
        msg_orin8.payload = frame_esp.payload;

        esp_heart_pub_->publish(msg_orin8);

        break;
    }

    case kb_interfaces::msg::Frame::ESP_HEALTH_STATUS: {
        // ESP32 sends at least 4 int32s: [flags, agc, heap_kb, i2c_errors], and
        // appends more over time — currently [steer_frames, steer_rejects,
        // steer_trip_age_s]. Four is the minimum this can split, not the count.
        if (frame_esp.payload.size() < 4) {
            RCLCPP_WARN(this->get_logger(),
                "ESP_HEALTH_STATUS: expected at least 4 int32s, got %zu",
                frame_esp.payload.size());
            break;
        }
        kb_interfaces::msg::Frame health_flags_msg;
        kb_interfaces::msg::Frame health_data_msg;
        health_flags_msg.type = frame_esp.type;
        health_data_msg.type = frame_esp.type;

        // flags only
        health_flags_msg.payload = {
            frame_esp.payload[0]
        };
        esp_health_flags_pub_->publish(health_flags_msg);

        // Everything except the flags word: agc, heap_kb, i2c_errors, then
        // whatever the firmware has appended since. Copied wholesale rather than
        // field by field, because the previous three-element literal silently
        // dropped every appended field — the steering frame counters never
        // reached the dashboard despite the firmware sending them, and the same
        // would have happened to steer_trip_age_s. Downstream decoding is
        // positional and length-guarded, so extra trailing fields are safe.
        health_data_msg.payload.assign(frame_esp.payload.begin() + 1,
                                       frame_esp.payload.end());
        esp_health_data_pub_->publish(health_data_msg);
        break;
    }

    case kb_interfaces::msg::Frame::ESP_DIAG_STEERING: {
        kb_interfaces::msg::Frame diag_steering_msg;
        diag_steering_msg.type = frame_esp.type;
        diag_steering_msg.payload = frame_esp.payload;
        esp_diag_steering_pub_->publish(diag_steering_msg);

        break;
    }

    case kb_interfaces::msg::Frame::ESP_PNEUMATIC: {
        // ESP32 sends 2 int32s: [tank_pressure_adc, compressor_duty (0-255, 0=off)]
        kb_interfaces::msg::Frame pneumatic_msg;
        pneumatic_msg.type = frame_esp.type;
        pneumatic_msg.payload = frame_esp.payload;
        esp_pneumatic_pub_->publish(pneumatic_msg);

        break;
    }

    case kb_interfaces::msg::Frame::ESP_STEER_PID: {
        // ESP32 reports the steering gains it is actually running, once per second:
        // [override, kp x1000, ki x1000, kd x1000, pwm_limit x1000]. Forwarded as-is;
        // the dashboard node does the scaling.
        kb_interfaces::msg::Frame steer_pid_msg;
        steer_pid_msg.type = frame_esp.type;
        steer_pid_msg.payload = frame_esp.payload;
        esp_steer_pid_pub_->publish(steer_pid_msg);

        break;
    }

    case kb_interfaces::msg::Frame::ESP_PEDALS: {
        // Driver pedals at ~20 Hz: [acc_mv, brake_mv, acc_effort, brake_effort].
        // mV = calibrated pin voltage (half the 0-5 V pedal signal, 10k/10k board
        // divider); effort = 0-255 from provisional firmware constants. Forwarded
        // as-is; the dashboard node does the scaling.
        kb_interfaces::msg::Frame pedals_msg;
        pedals_msg.type = frame_esp.type;
        pedals_msg.payload = frame_esp.payload;
        esp_pedals_pub_->publish(pedals_msg);

        break;
    }

    case kb_interfaces::msg::Frame::ESP_SAFETY_STATUS: {
        kb_interfaces::msg::Frame status;
        status.type = frame_esp.type;
        status.payload = frame_esp.payload;
        esp_safety_pub_->publish(status);
        break;
    }

    case kb_interfaces::msg::Frame::ESP_COMPLETE: {

        // Dividir mensaje en los submensajes que vienen en el payload
        kb_interfaces::msg::Frame msg_orin9;

        msg_orin9.type = frame_esp.type;
        msg_orin9.payload = frame_esp.payload;

        break;
    }

    default:
        break;
    }
}

// Callback de mensajes de la ORIN
void KB_coms_micro::kb_coms_TXcallback(const kb_interfaces::msg::Frame::SharedPtr msg) {

    serial_->send(msg->type, msg->payload);
}
