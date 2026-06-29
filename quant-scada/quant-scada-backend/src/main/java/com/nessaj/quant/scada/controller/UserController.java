package com.nessaj.quant.scada.controller;

import com.nessaj.quant.scada.auth.dto.UserInfoVO;
import com.nessaj.quant.scada.auth.entity.User;
import com.nessaj.quant.scada.auth.service.UserService;
import com.nessaj.quant.scada.common.CommonResult;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/user")
public class UserController {
    @Autowired
    private UserService userService;

    @GetMapping("/info")
    public CommonResult getUserInfo() {
        Long userId = (Long) SecurityContextHolder.getContext().getAuthentication().getPrincipal();
        User user = userService.findById(userId);
        if (user == null) {
            return CommonResult.error("用户不存在");
        }
        return CommonResult.ok(new UserInfoVO(user));
    }
}
