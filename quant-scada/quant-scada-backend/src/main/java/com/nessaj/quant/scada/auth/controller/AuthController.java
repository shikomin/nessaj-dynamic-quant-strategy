package com.nessaj.quant.scada.auth.controller;

import com.nessaj.quant.scada.auth.dto.LoginRequest;
import com.nessaj.quant.scada.auth.dto.RegisterRequest;
import com.nessaj.quant.scada.auth.dto.UserInfoVO;
import com.nessaj.quant.scada.auth.entity.User;
import com.nessaj.quant.scada.auth.service.UserService;
import com.nessaj.quant.scada.common.JwtUtil;
import com.nessaj.quant.scada.common.CommonResult;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.web.bind.annotation.*;

import javax.servlet.http.HttpServletRequest;
import javax.validation.Valid;
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.TimeUnit;

@RestController
@RequestMapping("/api/auth")
public class AuthController {
    @Autowired
    private UserService userService;

    @Autowired
    private JwtUtil jwtUtil;

    @Autowired
    private StringRedisTemplate redisTemplate;

    @PostMapping("/login")
    public CommonResult login(@Valid @RequestBody LoginRequest request) {
        User user = userService.findByUsername(request.getUsername());
        if (user == null) {
            return CommonResult.error("用户名或密码错误");
        }
        if (!userService.validatePassword(request.getPassword(), user.getPassword())) {
            return CommonResult.error("用户名或密码错误");
        }
        String token = jwtUtil.generateToken(user.getId(), user.getUsername());
        redisTemplate.opsForValue().set("token:" + token, String.valueOf(user.getId()), jwtUtil.getExpiration(), TimeUnit.MILLISECONDS);
        UserInfoVO userInfo = new UserInfoVO(user);
        Map<String, Object> data = new HashMap<>();
        data.put("token", token);
        data.put("user", userInfo);
        return CommonResult.ok(data);
    }

    @PostMapping("/register")
    public CommonResult register(@Valid @RequestBody RegisterRequest request) {
        User user = new User();
        user.setUsername(request.getUsername());
        user.setPassword(request.getPassword());
        user.setNickname(request.getNickname());
        user.setEmail(request.getEmail());
        user.setPhone(request.getPhone());
        userService.register(user);
        return CommonResult.ok("注册成功");
    }

    @PostMapping("/logout")
    public CommonResult logout(HttpServletRequest request) {
        String bearerToken = request.getHeader("Authorization");
        if (bearerToken != null && bearerToken.startsWith("Bearer ")) {
            String token = bearerToken.substring(7);
            redisTemplate.delete("token:" + token);
        }
        return CommonResult.ok("退出成功");
    }
}
