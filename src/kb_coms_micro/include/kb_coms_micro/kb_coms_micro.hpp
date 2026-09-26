#ifndef KB_COMS_MICRO_HPP_
#define KB_COMS_MICRO_HPP_

#include "kb_coms_serial_driver.hpp"
#include "kb_interfaces/msg/frame.hpp"
#include <rclcpp/rclcpp.hpp>
#include <std_msgs/msg/float32.hpp>
#include <atomic>
#include <chrono>

class KB_coms_micro : public rclcpp::Node {

  public:
    KB_coms_micro();

    ~KB_coms_micro();

    void kb_coms_OrinHeartbeat();

    void kb_coms_PublishFps();

    enum  class message_type_t : uint8_t{
      // ==========================
      // ESP32 --> Orin (0x01 - 0x1F)
      // ==========================
      ESP_ACT_SPEED           = 0x01,
      ESP_ACT_ACCELERATION    = 0x02,
      ESP_ACT_BRAKING         = 0x03,
      ESP_ACT_STEERING        = 0x04,
      ESP_MISION              = 0x05,
      ESP_MACHINE_STATE       = 0x06,
      ESP_ACT_SHUTDOWN        = 0x07,
      ESP_HEARTBEAT           = 0x08,
      ESP_COMPLETE            = 0x09,
      ESP_DIAG_STEERING       = 0x0A,
      ESP_HEALTH_STATUS       = 0x0B,
      ESP_PNEUMATIC           = 0x0C,
      ESP_STEER_PID           = 0x0D,
      ESP_PEDALS              = 0x0E,
      ESP_SAFETY_STATUS       = 0x0F,

      // ==========================
      // Orin --> ESP32 (0x20 - 0x3F)
      // ==========================
      ORIN_TARG_THROTTLE      = 0x20,
      ORIN_TARG_BRAKING       = 0x21,
      ORIN_TARG_STEERING      = 0x22,
      ORIN_MISION             = 0x23,
      ORIN_MACHINE_STATE      = 0x24,
      ORIN_HEARTBEAT          = 0x25,
      ORIN_SHUTDOWN           = 0x26,
      ORIN_COMPLETE           = 0x27,
      ORIN_CALIBRATE_STEERING = 0x28,
      ORIN_STEER_MODE         = 0x29,
      ORIN_COMPRESSOR_DISABLE = 0x2A,
      ORIN_STEER_PID          = 0x2B,
      ORIN_SAFETY_RESET       = 0x2C,

      // ==========================
      // Others (0x40 - 0xFF)
      // ==========================
    };

  private:
    void kb_coms_RXcallback(const SerialDriver::Frame &frame);

    void kb_coms_TXcallback(const kb_interfaces::msg::Frame::SharedPtr msg);

    rclcpp::Subscription<kb_interfaces::msg::Frame>::SharedPtr orin_safety_reset_sub_;

    std::unique_ptr<SerialDriver> serial_;

    rclcpp::TimerBase::SharedPtr timer_;
    rclcpp::TimerBase::SharedPtr fps_timer_;

    // ESP→Orin steering frame counter, sampled by fps_timer_ once per second.
    std::atomic<uint32_t> steering_frame_count_{0};
    std::chrono::steady_clock::time_point fps_last_sample_;

    rclcpp::Publisher<std_msgs::msg::Float32>::SharedPtr esp_fps_pub_;

    // Declaration of all publishers
    rclcpp::Publisher<kb_interfaces::msg::Frame>::SharedPtr esp_heart_pub_;
    rclcpp::Publisher<kb_interfaces::msg::Frame>::SharedPtr esp_speed_pub_;
    rclcpp::Publisher<kb_interfaces::msg::Frame>::SharedPtr esp_acceleration_pub_;
    rclcpp::Publisher<kb_interfaces::msg::Frame>::SharedPtr esp_braking_pub_;
    rclcpp::Publisher<kb_interfaces::msg::Frame>::SharedPtr esp_steering_pub_;
    rclcpp::Publisher<kb_interfaces::msg::Frame>::SharedPtr esp_mision_pub_;
    rclcpp::Publisher<kb_interfaces::msg::Frame>::SharedPtr esp_machine_state_pub_;
    rclcpp::Publisher<kb_interfaces::msg::Frame>::SharedPtr esp_shutdown_pub_;
    rclcpp::Publisher<kb_interfaces::msg::Frame>::SharedPtr esp_diag_steering_pub_;
    // health status
    rclcpp::Publisher<kb_interfaces::msg::Frame>::SharedPtr esp_health_flags_pub_;
    rclcpp::Publisher<kb_interfaces::msg::Frame>::SharedPtr esp_health_data_pub_;
    rclcpp::Publisher<kb_interfaces::msg::Frame>::SharedPtr esp_pneumatic_pub_;
    rclcpp::Publisher<kb_interfaces::msg::Frame>::SharedPtr esp_steer_pid_pub_;
    rclcpp::Publisher<kb_interfaces::msg::Frame>::SharedPtr esp_pedals_pub_;

    rclcpp::Publisher<kb_interfaces::msg::Frame>::SharedPtr esp_safety_pub_;

    // Declaration of all subscribers
    rclcpp::Subscription<kb_interfaces::msg::Frame>::SharedPtr orin_throttle_sub_;
    rclcpp::Subscription<kb_interfaces::msg::Frame>::SharedPtr orin_brake_sub_;
    rclcpp::Subscription<kb_interfaces::msg::Frame>::SharedPtr orin_steering_sub_;
    rclcpp::Subscription<kb_interfaces::msg::Frame>::SharedPtr orin_machine_state_sub_;
    rclcpp::Subscription<kb_interfaces::msg::Frame>::SharedPtr orin_mision_sub_;
    rclcpp::Subscription<kb_interfaces::msg::Frame>::SharedPtr orin_heartbeat_sub_;
    rclcpp::Subscription<kb_interfaces::msg::Frame>::SharedPtr orin_shutdown_sub_;
    rclcpp::Subscription<kb_interfaces::msg::Frame>::SharedPtr orin_steer_mode_sub_;
    rclcpp::Subscription<kb_interfaces::msg::Frame>::SharedPtr orin_compressor_disable_sub_;
    rclcpp::Subscription<kb_interfaces::msg::Frame>::SharedPtr orin_steer_pid_sub_;
};

#endif // KB_COMS_MICRO_HPP_
