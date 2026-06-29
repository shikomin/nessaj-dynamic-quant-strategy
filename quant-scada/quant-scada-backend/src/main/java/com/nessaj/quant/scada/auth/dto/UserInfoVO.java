package com.nessaj.quant.scada.auth.dto;

import com.nessaj.quant.scada.auth.entity.User;
import lombok.Data;

import java.time.LocalDateTime;

@Data
public class UserInfoVO {
    private Long id;
    private String username;
    private String nickname;
    private String email;
    private String phone;
    private LocalDateTime createdAt;

    public UserInfoVO(User user) {
        this.id = user.getId();
        this.username = user.getUsername();
        this.nickname = user.getNickname();
        this.email = user.getEmail();
        this.phone = user.getPhone();
        this.createdAt = user.getCreatedAt();
    }
}
